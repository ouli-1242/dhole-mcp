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


def test_the_default_connect_is_the_published_32_496():
    """不设 DHOLE_TOOLS 时，wire 就是 CONNECT_CEILING 那笔账：32,496。

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

    30,719 → 31,208（2026-10-10，体验修复刀3-C3）：一次**上抬 +489**，买的是地图
    的大小从此有主人。`max_total_chars` 从前只在 web_crawl 那颗门上，`web_discover`
    转发时不传 → 一张 1,000 行的地图（实测 116,173 字符 / ~32k token）把默认预算
    透支 1.9 倍而 `truncated_by_total_chars` 一路是 False；预算现在数的是序列化后的
    行。被切掉的行要有路可拿，否则修的就是一次静默截断，所以 `resume_token` 也上
    web_discover 这扇门（读已存下的地图，不走网络）。两颗参数行 +332、描述尾句 +157。

    31,208 → 31,423（2026-10-10，体验修复刀3-C5）：一次**上抬 +215**，买的是
    `timeout=` 从此不说两句单位。它声明的是整次调用的毫秒（max 120000），而 HTTP 一档
    钳的是每次尝试的秒（30s），一份文档又只有一次尝试 —— 那句话在"大文件请抬 timeout"
    这条真实用法上是一句够不着的承诺。三颗描述各补上限（web_read +98 / web_crawl +66 /
    web_request +52），`feed_read` 顺手把印错的默认值说回 20s（−1：那颗参数从前根本
    没换算，毫秒被当秒用）。收据本身（`timeout_applied`）是响应字段，不占这一页。

    31,423 → 31,451（2026-10-10，体验修复刀3-A2）：一次**上抬 +28**，买的是三颗**必填**
    载荷键上了 `web_act.actions`。那句清单从前数完 11 个动作名就只说目标怎么寻址，一个
    载荷键都没点名 —— `text` 与 `key` 在任何 wire 文本里都不存在，只读 `tools/list` 的
    调用方只能猜（实测猜错两次），而答案只住在报错里：那等于要求先失败一次。整表（含可选
    旋钮）的第一版是 519 字符，撞了 `test_wire_shape.py` 那颗 296 的闸；按那道闸自己的
    口径（搬家，不是缩写）可选项留在 references/tools.md，wire 只留必填的那三颗。
    31,451 → 31,824（2026-10-10，体验修复刀3-A3）：一次**上抬 +373**，买的是把一句做不到
    的承诺换成一对用得上的形状。常驻层写着 "Captchas get at most 3 read-and-retry rounds"，
    而每次调用都重新导航 —— 第二轮读到的挑战已经不是第一轮那一枚（实测靶站每次 GET 出新
    token）。同一句假话的第二个作者是 VISUAL ASSIST 那条：截图"就是你马上要照着打字的那一枚"，
    可那张图要等整条链跑完才交得到调用方眼前。新的两半说的是结构与那一种真能做的：图像挑战
    转交；页面把挑战写成文本时，`{"read": {..., "as": "code"}}` 再 `{"fill": 
    {"text": "{=code}"}}` 在同一条链里读完。动作清单 +6，工具描述 +367。
    31,824 → 32,466（2026-10-10，体验修复刀3-W1）：一次**上抬 +642**，买的是这一批里
    只剩最后一格的那句"调用方对自己那一发请求没有任何说法"。一次零点名的 `web_read`
    实测发 15 颗头 —— 一枚编造的 `referer: https://www.google.com/`、browserforge 的 UA
    与 `sec-ch-ua*` 一家（16 发抽样里 3 发把手机 UA 配在 `sec-ch-ua-platform: "Linux"`，
    13 发带着 `sec-fetch-site: "?1"` 那个错值），而旧参数面上没有任何一颗能改它。
    `headers=` 上 web_read（+386）与 web_request（+256）；同一刀把 `stealthy_headers`
    那两颗字面量交给 `anti_detect` 档位表读，公开面因此**没有**多第二颗伪装旋钮。
    三条设计边界（凭据 / UA / 校验子）各带自己那扇门的地址，而计划里那句
    "primp 编译层覆盖不掉" 被实测推翻了 —— 覆盖得掉，所以拒绝名单上没有"做不到"这一类。

    32,466 → 32,496（2026-10-10，体验修复刀3-D2）：一次**上抬 +30**，买的是常驻层不再对
    一台三个月前批过的主机说"这是第一次"。名册从前是无时间戳的裸 host 列表，三月的按键到
    十月还在放行；现在磁盘形状是 `{host: 批准的 ISO 时间戳}`，过窗（30 天）重新征询而不是
    静默失效。三处描述改词：web_read.confirm +24、web_read.cdp_url +4、web_act.cdp_url +2
    （instructions 一字不变，2,476 那一半属于路由行而不是这次）。
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
    assert ruler_compact + ruler_instr == 32_496


def test_the_two_tool_daily_driver_pays_what_its_docstring_claims():
    """`DHOLE_TOOLS=web_read,web_search` 的真机价：11,894（上一版注释写 11,480）。

    四次移动的账：11,423 → 11,382 是把签名影子摘掉时顺带瘦的字（那一刀量过真机），
    11,382 → 11,480 是 C5 给 `web_read` 的 `timeout` 补上"一次尝试封顶 30s"那一句
    （+98：web_read 在这一组里，`web_search` 那颗不谈下载所以没付），11,480 → 11,866
    是 W1 的 `headers=`（+386：这一组里只有 `web_read` 付它 —— `web_search` 不发请求，
    而一颗搜索的参数长不出一条 HTTP 头的账）。11,866 → 11,894 是 D2 的
    `confirm`/`cdp_url` 两句（+28：两颗都在 `web_read` 身上，`web_act` 那 +2 不在这一组）。
    同一条拒绝名单在 `web_request` 那一扇门上只花 256，因为它的描述指着
    `web_read.headers` 而不是重说一遍。
    """
    assert _assert_price("web_read,web_search",
                         ["web_read", "web_search"]) == 11_894


def test_the_readme_eight_tool_price_is_what_the_server_sends():
    """README 的 8 颗价不再只是算术 —— 真机同一值，且文档印的就是它（24,484）。"""
    _assert_price(",".join(SUB8), SUB8,
                  published=("README.md", "这 8 颗是 {total} 字符"))


def test_the_ninth_tool_costs_its_own_row_and_no_routing_line():
    """加 web_discover 只多它自己那一行：路由行与 web_crawl 共用，去重后不再付第二次。

    这是 F-A-J1 那把共享路由行在 wire 上的样子：README 的 9 颗 26,541 =
    24,484 + 2,057，instructions 一字不变。C3 之后 `web_discover` 自己那一行涨了
    （地图大小两颗旋钮上这门），涨的部分照旧只付一次；C5 没动它（那一扇门一次尝试都不
    走 HTTP 正文通道，所以它不欠那句上限）。D2 的 +28 全部落在 `web_read`，所以这一组
    与 8 颗那组同幅上涨。
    """
    nine = SUB8 + ["web_discover"]
    assert _assert_price(",".join(nine), nine,
                         published=("README.md", "共 9 颗 {total}")) == 26_541
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
