"""End-to-end MCP protocol tests — RUN MANUALLY, not in CI.

These tests spawn a real `dhole.exe` subprocess as a stdio MCP server
and verify the wire protocol against it. They are NOT pytest-discovered
on CI because:
  - Spawning dhole per test in 6 matrix cells costs ~1 minute per cell.
  - The subprocess side-effects (live HTTP fetches) cost time and money.
  - Unit tests in test_server.py already cover the underlying handlers
    with mocks and are the canonical regression net.

Run manually:
    pytest -m e2e tests/e2e_mcp_test.py -v
Or run as a smoke script:
    python tests/e2e_mcp_test.py

If a regression lands in the MCP stdio wire protocol, run the e2e
tests against the local dhole binary before tagging a release.
"""
import json
import subprocess
import sys
import time

import pytest

# All tests in this module are tagged `e2e`. CI default conftest
# skips them via `addopts = "-m 'not e2e'"` in pyproject.toml.
pytestmark = pytest.mark.e2e

# Safety net: cap consecutive empty readlines. The MCP server, when
# happy, returns a newline-terminated JSON line on the first readline.
# When it crashes or emits a non-JSON banner before our initialize,
# readline can return "" repeatedly. Without this cap the test hangs
# indefinitely waiting for data that will never arrive.
_MAX_EMPTY_READS = 50


class MCPClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            ["dhole"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**__import__("os").environ},
        )
        self._id = 0
        self._send("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "1.0"},
        })

    def _send(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        return self._read_line_json()

    def _read_line_json(self):
        """Read one newline-terminated JSON line. Bounded by _MAX_EMPTY_READS."""
        empty_count = 0
        while True:
            # If the server died, EOF returns "" forever. Detect that
            # immediately by checking poll() before even trying to read.
            if self.proc.poll() is not None:
                tail = _drain_stderr(self.proc)
                raise RuntimeError(
                    f"Server exited (code {self.proc.returncode}) mid-call. "
                    f"stderr tail: {tail}"
                )
            line = self.proc.stdout.readline()
            if not line:
                empty_count += 1
                if empty_count > _MAX_EMPTY_READS:
                    tail = _drain_stderr(self.proc)
                    raise RuntimeError(
                        f"Server produced {_MAX_EMPTY_READS} consecutive "
                        f"empty readlines. Aborting. stderr tail: {tail}"
                    )
                # Brief sleep so a dead server is detected via poll()
                # instead of purely via OS readline blocking on a closed pipe.
                time.sleep(0.05)
                continue
            text = line.decode().strip()
            if not text:
                # Blank line — JSON-RPC servers tend to emit these only
                # on broken pipes. Keep going but count it.
                empty_count += 1
                if empty_count > _MAX_EMPTY_READS:
                    raise RuntimeError("Too many empty lines.")
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Server emitted non-JSON line: {text[:200]!r} ({exc}). "
                    f"stderr: {_drain_stderr(self.proc)}"
                )

    def call_tool(self, name, args):
        return self._send("tools/call", {"name": name, "arguments": args})

    def list_tools(self):
        return self._send("tools/list", {})

    def close(self):
        if self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _drain_stderr(proc, max_bytes: int = 2000) -> str:
    """Best-effort stub drain so an error message can include server stderr."""
    try:
        buf = []
        start = time.monotonic()
        while time.monotonic() - start < 0.2:
            if proc.stderr is None:
                break
            chunk = proc.stderr.read1(1024)
            if not chunk:
                break
            buf.append(chunk)
        return b"".join(buf)[-max_bytes:].decode("utf-8", errors="replace")
    except Exception:
        return "(unavailable)"


@pytest.fixture
def mcp():
    client = MCPClient()
    try:
        yield client
    finally:
        client.close()


def test_tool_definitions(mcp):
    result = mcp.list_tools()
    tools = result.get("result", {}).get("tools", [])
    tool_map = {t["name"]: t for t in tools}

    fetch_desc = tool_map["web_read"]["description"]
    # 14.7 的「描述瘦身」删掉了旧文案里的 "EVERY web page"，这条断言从那时起就一直
    # 挂着 —— 等于每版都在骗自己「e2e 只是没跑」。钉住现在真正要守的外部契约：
    # robots 遵从、跨调用会话、动词、archive 层、自查清单。
    #
    # 挂在**可见面**上而不是某一个界面上：16.0 的审计把 envelope 读法从八个工具描述
    # 各抄一遍上收到 instructions 一份；17.0 拆平之后参数说明各自住在自己的 property
    # 上。只查 tool.description 会判「删了」，而那句话只是换了一个每次连接都会到的
    # 地方 —— 对 agent 而言这些段是同一句话。
    props = tool_map["web_read"]["inputSchema"]["properties"]
    from dhole_mcp.server import DHOLE_INSTRUCTIONS
    visible = (fetch_desc + DHOLE_INSTRUCTIONS
               + "".join((p.get("description") or "")
                         for p in props.values() if isinstance(p, dict)))
    assert "robots" in fetch_desc.lower()
    assert "session_id" in visible
    # 两条陷阱都要在可见面上露名：method 的"非 GET 只发一次"和 body 的 256KB 上限。
    # 判据写的是文本里真的那句承诺，不是"body 这个词出现过"—— 后者连"body part"都能满足。
    assert "sent ONCE" in visible and "cap 256KB" in visible, \
        "verb/body 的陷阱句从可见面上消失了"
    assert "archive.org" in visible
    assert "do not cite" in visible.lower(), "引用前的自查必须在场"
    assert "snippets" in visible.lower()
    assert "offset" in visible.lower()
    # 'html' 不靠散文露脸：它是 extraction_type 的枚举值，而枚举才是客户端取值的来源
    assert "html" in tool_map["web_read"]["inputSchema"]["properties"][
        "extraction_type"]["enum"]
    # schema 是结构化提取的唯一入口，此前会被静默丢弃（传了也照样返回 markdown），
    # 所以这里把它钉在 e2e 层面：既要在描述里露脸，也要在 inputSchema 里真实存在。
    assert "schema" in visible
    assert "schema" in tool_map["web_read"]["inputSchema"]["properties"]

    offset_desc = tool_map["web_read"]["inputSchema"]["properties"]["offset"]["description"]
    assert "next_offset" in offset_desc

    # 17.0 拆平之后，这些键必须**出现在 properties 里** —— 那是客户端取值的唯一
    # 来源。以前它们只活在袋子那段散文里，漏写一个键等于功能在 wire 上不存在；现在
    # 漏声明一个键连调用都发不出去，所以这条查的比原先硬。
    for key in ("session_id", "method", "body", "content_type",
                "allow_private", "ignore_robots", "max_links", "auth"):
        assert key in props, f"{key} 没被声明，agent 写不出来"
        assert (props[key].get("description") or "").strip(), f"{key} 声明了却没说明"
    # 16.0 的另一半：搜索的日期窗口与 feed 的增量，各自是一个参数。
    ss_props = tool_map["web_search"]["inputSchema"]["properties"]
    assert "after" in ss_props and "before" in ss_props
    assert "refused, not ignored" in ss_props["before"]["description"].lower(), \
        "before 是拒绝而不是忽略 —— 这句才是调用方要的行为（大小写不钉，文案改过字体）"
    assert "since" in tool_map["feed_read"]["inputSchema"]["properties"]

    # 搜索缓存：web_search 的散文不再提它，但 cache_ttl 是它的公开参数。
    assert "cache_ttl" in ss_props

    cache_desc = tool_map["sys_state"]["description"]
    assert "TTL" in cache_desc or "ttl" in cache_desc or "cache stores" in cache_desc.lower()


def test_urls_alone_clears_the_protocol_gate(mcp):
    """`urls=[...]` 不带 `url` 必须走得通整条 wire。

    `inputSchema` 的 anyOf 承诺两种形状（url= 或 urls=），协议层却可能在请求到达
    服务端之前就把其中一种拒掉 —— 承诺的形状发不出去。单元测试直接调分发器，恰好
    绕过这道网关，所以只有这条测得到。袋子时代它还要额外证明"urls 藏在袋里也算
    第三种形状"，现在只有一条通道，测的就是 anyOf 的第二支。
    """
    resp = mcp.call_tool("web_read", {
        "urls": ["https://example.com"], "max_content_chars": 600, "cache_ttl": 0,
    })
    result = resp.get("result", {})
    assert result.get("isError") is False, result
    data = _extract_data(result)
    assert data.get("total") == 1 and data.get("successful") == 1, data
    assert (data.get("results") or [{}])[0].get("url") == "https://example.com", data


def test_smart_fetch_response_fields(mcp):
    resp = mcp.call_tool("web_read", {"url": "https://example.com", "cache_ttl": 0})
    data = _extract_data(resp.get("result", {}))
    assert data.get("total_extracted_chars", 0) > 0
    is_trunc = data.get("is_truncated", False)
    next_off = data.get("next_offset", 0)
    if is_trunc:
        assert next_off > 0
    else:
        assert next_off == 0


def test_method_reaches_the_http_tier(mcp):
    """wire 上的 method 真的走到 HTTP 层：一次 HEAD 探针。

    单元测试是在同一个进程里直接调 ``web_read(...)``，绕过了「wire arguments →
    分发器 → 形参」这一段。HEAD 是这一段最短的探针：不抽取正文、不升级浏览器，
    一次请求就有答案。
    """
    resp = mcp.call_tool(
        "web_read",
        {"url": "https://example.com", "cache_ttl": 0, "method": "HEAD"},
    )
    data = _extract_data(resp.get("result", {}))
    assert data.get("status") == 200, data.get("error")
    assert data.get("content_ok") is True, data
    # HEAD 按定义没有正文；把「没有」报成 0 字节 markdown 才是撒谎。
    assert not "".join(data.get("content") or [])
    assert "head probe" in data.get("summary", "").lower()


def test_revalidation_options_are_off_the_wire(mcp):
    """条件请求参数在 17.0 下了线（§6），wire 上必须两样都成立：不在 schema 里，
    传进来还被拒。

    这一条原来是「if_none_match 走完 wire 到真实源站」。被删掉的只是**发起权**：
    复验还在跑，发起者换成了 watch，它经由进程内的 `_REVALIDATE` 带标记，那边由
    tests/test_revalidation_g23.py 的真 loopback 源站钉 304。wire 这一层能钉的是
    反面 —— 静默忽略老键会让人以为自己的条件请求发出去了，而实际拿到的是整页。
    """
    tools = mcp.list_tools().get("result", {}).get("tools", [])
    by_name = {t["name"]: t for t in tools}
    for key in ("if_modified_since", "if_none_match"):
        for tool in ("web_read", "feed_read"):
            assert key not in by_name[tool]["inputSchema"]["properties"], \
                f"{tool} 还挂着 {key}"

    for key in ("if_none_match", "if_modified_since"):
        resp = mcp.call_tool("web_read", {
            "url": "https://httpbin.org/get", key: "*"})
        blob = json.dumps(resp, ensure_ascii=False)
        assert "Unsupported argument" in blob, blob[:400]
        assert key in blob, blob[:400]


def test_search_date_window_reaches_the_engines(mcp):
    """after= 走完 wire → 搜索层 → 引擎，并且如实报告发了哪一档。"""
    resp = mcp.call_tool("web_search", {
        "query": "python asyncio tutorial",
        "after": "2026-09-20", "max_results": 3, "cache_ttl": 0})
    data = _extract_data(resp.get("result", {}))
    assert not str(data.get("error", "")).startswith("invalid_request"), data.get("error")
    window = data.get("date_filter") or {}
    assert window.get("requested", "").startswith("after=2026-09-20")
    assert window.get("sent", "").startswith("freshness=")
    # 默认池里有三家根本不接受日期条件，必须点名，否则「按日期搜过」是句空话。
    assert window.get("engines_unfiltered")


def test_error_response_format(mcp):
    # 参数错从 14.x 起不再抛异常，而是**信封里**的一条 invalid_request + next_action
    # 让 agent 照着改（isError=True 会让多数客户端只把文本倒给用户，结构化的
    # next_action 就白给了）。这条契约由 test_bug_report* 守着；旧断言在它失效之后
    # 还挂了 8 个版本，因为默认跑法把 e2e 排除在外。
    resp = mcp.call_tool("web_read", {})
    result = resp.get("result", {})
    assert result.get("isError") is False, result
    data = _extract_data(result)
    assert str(data.get("error", "")).startswith("invalid_request:")
    assert data.get("content_ok") is False
    assert data.get("next_action")

    # 不认识的参数名同样在信封里报错，且不静默忽略（静默忽略 = 调用方以为生效了）。
    # 名字得是**真的**不认识：cache_ttl 是合法参数，拿它当「未知键」测等于什么都没测。
    bad = _extract_data(mcp.call_tool("web_read", {
        "url": "https://example.com", "cache_tll": 0,
    }).get("result", {}))
    assert "Unsupported argument" in str(bad.get("error", "")), bad
    assert "cache_ttl" in str(bad.get("error", "")), "支持集要一起给出，否则改不出正确的键"

    resp2 = mcp.call_tool("nonexistent_tool_xyz", {})
    assert resp2.get("result", {}).get("isError", False) is True


def test_bulk_url_limit(mcp):
    fake_urls = [f"https://example{i}.com" for i in range(101)]
    resp = mcp.call_tool("web_read", {"urls": fake_urls})
    result = resp.get("result", {})
    assert result.get("isError", False) is True, \
        f"101 URLs should trigger error. isError={result.get('isError')}"


def _extract_data(result: dict) -> dict:
    if "structuredContent" in result:
        return result["structuredContent"]
    content = result.get("content", [])
    if content:
        try:
            return json.loads(content[0].get("text", ""))
        except Exception:
            pass
    return {}


if __name__ == "__main__":
    print("End-to-end MCP protocol tests for Dhole\n")
    print("(Subprocess-per-test; pass 1+/cell takes ~30s)\n")
    exit_code = pytest.main([__file__, "-v", "-x"])
    sys.exit(exit_code)
