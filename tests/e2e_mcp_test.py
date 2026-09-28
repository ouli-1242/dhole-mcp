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

    fetch_desc = tool_map["smart_fetch"]["description"]
    # 14.7 的「描述瘦身」删掉了旧文案里的 "EVERY web page"，这条断言从那时起就一直
    # 挂着 —— 等于每版都在骗自己「e2e 只是没跑」。钉住现在真正要守的外部契约：
    # robots 遵从、跨调用会话、动词、archive 层、自查清单。
    #
    # 挂在**可见面**上而不是某一个界面上：16.0 的审计把 envelope 读法从八个工具描述
    # 各抄一遍上收到 instructions 一份，选项键则按设计只出现在 options 那段文本里
    # （它们不进 properties）。只查 tool.description 会判「删了」，而那句话只是换了
    # 一个每次连接都会到的地方 —— 对 agent 而言这三段是同一句话。
    options_desc = tool_map["smart_fetch"]["inputSchema"]["properties"]["options"]["description"]
    from dhole_mcp.server import DHOLE_INSTRUCTIONS
    visible = fetch_desc + options_desc + DHOLE_INSTRUCTIONS
    assert "robots" in fetch_desc.lower()
    assert "session_id" in visible
    assert "method:" in visible and "body:" in visible
    assert "archive.org" in visible
    assert "do not cite" in visible.lower(), "引用前的自查必须在场"
    assert "snippets" in visible.lower()
    assert "offset" in visible.lower()
    # 'html' 不靠散文露脸：它是 extraction_type 的枚举值，而枚举才是客户端取值的来源
    assert "html" in tool_map["smart_fetch"]["inputSchema"]["properties"][
        "extraction_type"]["enum"]
    # schema 是结构化提取的唯一入口，此前会被静默丢弃（传了也照样返回 markdown），
    # 所以这里把它钉在 e2e 层面：既要在描述里露脸，也要在 inputSchema 里真实存在。
    assert "schema" in visible
    assert "schema" in tool_map["smart_fetch"]["inputSchema"]["properties"]

    offset_desc = tool_map["smart_fetch"]["inputSchema"]["properties"]["offset"]["description"]
    assert "next_offset" in offset_desc

    # 选项袋（options={...}）里的键**不进** inputSchema.properties —— 那是本项目刻意
    # 的契约（`_promote_options` 负责提升，test_tool_descriptions.py 守键集合）。于是
    # agent 能不能发现它们，只取决于 options 那段描述文本。16.0 新加的
    # session_id / method / body / content_type 就靠这条兜住：漏写一个键，那个功能
    # 在 wire 上就等于不存在。
    for key in ("session_id", "method", "body", "content_type",
                "allow_private", "ignore_robots", "max_links", "auth",
                "if_modified_since", "if_none_match"):
        assert key in options_desc
    # 16.0 的另一半：搜索的日期窗口与 feed 的增量。这两条都只存在于选项文本里
    # （选项键不进 properties），漏写就等于功能在 wire 上不存在。
    ss_desc = tool_map["smart_search"]["inputSchema"]["properties"]["options"]["description"]
    # 「一行一键」的排版之后 after/before 挤在 freshness 那一行里：钉键名和各自那句话，
    # 不钉括号写法（16.0 之前长这样："after (...)"）。
    assert "after:" in ss_desc and "before:" in ss_desc
    assert "refused" in ss_desc, "before 是拒绝而不是忽略 —— 这句才是调用方要的行为"
    assert "since" in tool_map["feed_fetch"]["inputSchema"]["properties"]

    # 搜索缓存：13.16 重写描述后这句提示只在 cache_clear 里，smart_search 的散文
    # 不再提它 —— 但 cache_ttl 仍是它的公开选项，所以断言挂在选项上而不是散文上。
    assert "cache_ttl" in (
        tool_map["smart_search"]["inputSchema"]["properties"]["options"]["description"]
    )

    cache_desc = tool_map["cache_clear"]["description"]
    assert "TTL" in cache_desc or "ttl" in cache_desc or "cache stores" in cache_desc.lower()


def test_urls_in_the_options_bag_clears_the_protocol_gate(mcp):
    """`options.urls` 必须走得通整条 wire。

    选项描述明写袋里接受 `urls`，分发器也确实会提升它（进程内实测 total=1）——
    而 `inputSchema` 的 anyOf 只列了顶层两种，于是协议层在请求到达服务端之前就报
    "must have required property 'url'"：承诺的形状发不出去。单元测试直接调分发器，
    恰好绕过这道网关，所以只有这条测得到。
    """
    resp = mcp.call_tool("smart_fetch", {"options": {
        "urls": ["https://example.com"], "max_content_chars": 600, "cache_ttl": 0,
    }})
    result = resp.get("result", {})
    assert result.get("isError") is False, result
    data = _extract_data(result)
    assert data.get("total") == 1 and data.get("successful") == 1, data
    assert (data.get("results") or [{}])[0].get("url") == "https://example.com", data


def test_smart_fetch_response_fields(mcp):
    resp = mcp.call_tool("smart_fetch", {"url": "https://example.com", "cache_ttl": 0})
    data = _extract_data(resp.get("result", {}))
    assert data.get("total_extracted_chars", 0) > 0
    is_trunc = data.get("is_truncated", False)
    next_off = data.get("next_offset", 0)
    if is_trunc:
        assert next_off > 0
    else:
        assert next_off == 0


def test_options_bag_reaches_the_http_tier(mcp):
    """选项袋里的 method 真的走到 HTTP 层：一次 HEAD 探针。

    单元测试是在同一个进程里直接调 ``smart_fetch(...)``，绕过了「wire arguments →
    ``_promote_options`` → 形参」这一段。HEAD 是这一段最短的探针：不抽取正文、
    不升级浏览器，一次请求就有答案。
    """
    resp = mcp.call_tool(
        "smart_fetch",
        {"url": "https://example.com", "cache_ttl": 0, "options": {"method": "HEAD"}},
    )
    data = _extract_data(resp.get("result", {}))
    assert data.get("status") == 200, data.get("error")
    assert data.get("content_ok") is True, data
    # HEAD 按定义没有正文；把「没有」报成 0 字节 markdown 才是撒谎。
    assert not "".join(data.get("content") or [])
    assert "head probe" in data.get("summary", "").lower()


def test_revalidation_option_reaches_the_origin(mcp):
    """options.if_none_match 走完 wire 到 HTTP 层：一次对真实源站的条件请求。

    loopback 那组用例答的是可重复的 304，这里要的是真实源站怎么处理这个头：它答
    304 就必须是「成功、无正文」，它不理条件请求（很多站点就这样）就回普通 200，
    两种形状都不能被报成失败。
    """
    resp = mcp.call_tool("smart_fetch", {
        "url": "https://httpbin.org/get", "cache_ttl": 0,
        "options": {"if_none_match": "*"}})
    data = _extract_data(resp.get("result", {}))
    body = "".join(data.get("content") or [])
    assert data.get("status") in (200, 304), data.get("error")
    if data.get("status") == 304:
        assert data.get("not_modified") is True
        assert data.get("content_ok") is True, "304 是成功，不是失败"
        assert not body
    else:
        # 缺席即默认：not_modified=false 已经不上线（16.0 的响应压缩），读法要跟契约走
        assert data.get("not_modified", False) is False
        # 「头真的到达源站」的证据在正文里：httpbin 把收到的请求头回显出来。
        # cache_validators 不算证据 —— 源站不发 etag/last-modified 时它是 {}，
        # 而 {} 按同一条规则不上线，所以这条断言在压缩之后只会自证失败。
        assert "If-None-Match" in body, body[:300]


def test_search_date_window_reaches_the_engines(mcp):
    """options.after 走完 wire → 搜索层 → 引擎，并且如实报告发了哪一档。"""
    resp = mcp.call_tool("smart_search", {
        "query": "python asyncio tutorial",
        "options": {"after": "2026-09-20", "max_results": 3, "cache_ttl": 0}})
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
    resp = mcp.call_tool("smart_fetch", {})
    result = resp.get("result", {})
    assert result.get("isError") is False, result
    data = _extract_data(result)
    assert str(data.get("error", "")).startswith("invalid_request:")
    assert data.get("content_ok") is False
    assert data.get("next_action")

    # 不认识的选项键同样在信封里报错，且不静默忽略（静默忽略 = 调用方以为生效了）。
    # 键得是**真的**不认识：cache_ttl 从 16.0 起是袋里的合法键（G22 把 smart_fetch
    # 除 url 外的每个参数都收成两处放法），拿它当「未知键」测等于什么都没测。
    bad = _extract_data(mcp.call_tool("smart_fetch", {
        "url": "https://example.com", "options": {"cache_tll": 0},
    }).get("result", {}))
    assert "Unsupported option key" in str(bad.get("error", "")), bad
    assert "cache_ttl" in str(bad.get("error", "")), "支持集要一起给出，否则改不出正确的键"

    resp2 = mcp.call_tool("nonexistent_tool_xyz", {})
    assert resp2.get("result", {}).get("isError", False) is True


def test_bulk_url_limit(mcp):
    fake_urls = [f"https://example{i}.com" for i in range(101)]
    resp = mcp.call_tool("smart_fetch", {"urls": fake_urls})
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
