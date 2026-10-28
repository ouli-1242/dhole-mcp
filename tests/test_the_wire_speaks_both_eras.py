"""同一份 wire 说两种代际（2026-10-25 钉子）。

MCP 2026-07-28 GA 把「无状态核心」定为新时代：请求自带
`Mcp-Protocol-Version: 2026-07-28` 头 + `_meta` 自述信封（协议版本 + 客户端
能力）+ `Mcp-Method`/`Mcp-Name` 头路由，不再有 initialize 握手与会话；
发现走 `server/discover`。SDK 2.2.0 的传输层按头做 era-routing：握手表
（`HANDSHAKE_PROTOCOL_VERSIONS`）有意止步于 2025-11-25，其余值进无状态通道。

真实使用模拟（review/manual-2026-10-25）证明这条路在 dhole 上端到端可用，
但「可用」与「受支持」隔着一层：loop 时代记录的「dhole 最高 2025-11-25」
是旧 SDK 下的过期事实，没有任何钉子拦住它再变回去。本文件把两种代际都钉成
真 SDK 回归 —— 握手代际走 in-memory 回路（与全仓其它测试同款），新代际走
真 streamable HTTP ASGI 应用（与 `--http` 同一套构造，不带预热与令牌门）。

反例锁也在这里：一次**握手形态**的 initialize 要 2026-07-28，协商诚实地
落回 2025-11-25 —— 新代际客户端根本不 initialize，这个回落不是缺陷，把它
「修」成回显 2026-07-28 才是（那等于谎称握手代际支持它没实现的东西）。
"""


from __future__ import annotations


import json

import pytest

from dhole_mcp.server import MasterFetchServer

pytestmark = pytest.mark.smoke

ACCEPT = {"Accept": "application/json, text/event-stream",
          "Content-Type": "application/json"}
MODERN = "2026-07-28"
LEGACY_LATEST = "2025-11-25"
ENVELOPE = {"io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientCapabilities": {}}


def _body_text(r) -> str:
    body = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        data = None
        for line in body.splitlines():
            if line.startswith("data:"):
                data = line[5:].strip()
        body = data or "{}"
    return body


# ─── 握手代际（in-memory，与全仓同款回路）──────────────────────────────


@pytest.mark.asyncio
async def test_the_handshake_era_still_serves_the_full_face():
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert ({t.name for t in listing.tools}
                    == {d["name"] for d in MasterFetchServer._TOOL_DEFS}), \
                sorted(t.name for t in listing.tools)


# ─── 2026-07-28 无状态代际（真 streamable HTTP ASGI 应用）───────────────


@pytest.mark.asyncio
async def test_the_modern_era_serves_discover_list_and_call_without_a_session():
    """不 initialize、不带会话：头路由 + 自述信封直发三发。

    这是 2026-07-28 客户端的连接形状 —— 它从不存在「会话」，每一发请求自己
    说自己是谁。server/discover 是新时代的发现门（取代 initialize 的那一问）。
    """
    import httpx
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(app=MasterFetchServer()._mcp_server())
    async with manager.run():
        transport = httpx.ASGITransport(
            app=lambda scope, receive, send: manager.handle_request(
                scope, receive, send))
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            headers = {**ACCEPT, "Mcp-Protocol-Version": MODERN}

            r = await client.post(
                "/mcp", headers={**headers, "Mcp-Method": "server/discover"},
                json={"jsonrpc": "2.0", "id": 1, "method": "server/discover",
                      "params": {"_meta": dict(ENVELOPE)}})
            assert r.status_code == 200, r.text[:200]
            discovered = json.loads(_body_text(r)).get("result") or {}
            assert (discovered.get("capabilities") or {}).get("tools"), discovered
            assert "web_read" in (discovered.get("instructions") or "")

            r = await client.post(
                "/mcp", headers={**headers, "Mcp-Method": "tools/list"},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                      "params": {"_meta": dict(ENVELOPE)}})
            assert r.status_code == 200, r.text[:200]
            tools = [t.get("name") for t in
                     (json.loads(_body_text(r)).get("result") or {}).get("tools")
                     or []]
            assert set(tools) == {d["name"] for d in MasterFetchServer._TOOL_DEFS}, tools

            r = await client.post(
                "/mcp",
                headers={**headers, "Mcp-Method": "tools/call",
                         "Mcp-Name": "sys_guide"},
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "sys_guide",
                                 "arguments": {"topic": "start"},
                                 "_meta": dict(ENVELOPE)}})
            assert r.status_code == 200, r.text[:200]
            call = json.loads(_body_text(r)).get("result") or {}
            # 裸 JSON 回执里 content 是 dict（in-memory 那条路才是对象）
            assert any((b.get("text") if isinstance(b, dict) else
                        getattr(b, "text", "")) for b in call.get("content") or [])


@pytest.mark.asyncio
async def test_a_modern_request_without_the_self_describing_envelope_is_refused_structured():
    """新代际的每发请求必须自带信封；缺了它，拒绝要成结构（不是 500 也不是沉默）。"""
    import httpx
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(app=MasterFetchServer()._mcp_server())
    async with manager.run():
        transport = httpx.ASGITransport(
            app=lambda scope, receive, send: manager.handle_request(
                scope, receive, send))
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            r = await client.post(
                "/mcp", headers={**ACCEPT, "Mcp-Protocol-Version": MODERN,
                                 "Mcp-Method": "server/discover"},
                json={"jsonrpc": "2.0", "id": 1, "method": "server/discover",
                      "params": {}})
            assert r.status_code == 400, r.text[:200]
            err = (json.loads(_body_text(r)).get("error") or {})
            assert err.get("code") == -32602, err
            assert "protocolVersion" in str(err.get("message")), err


@pytest.mark.asyncio
async def test_a_handshake_initialize_asking_for_the_modern_version_negotiates_down():
    """反例锁：握手形态的 initialize 要 2026-07-28，诚实落回 2025-11-25。

    新代际客户端不 initialize（它没有会话，每一发自述）；会发 initialize 的
    是旧代际客户端，它拿到的是握手表里最接近的那一份。把这次回落「修」成
    回显 2026-07-28，等于谎称握手代际支持它没实现的东西 —— 这颗钉子钉住
    「不许那样修」。
    """
    import httpx
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    manager = StreamableHTTPSessionManager(app=MasterFetchServer()._mcp_server())
    async with manager.run():
        transport = httpx.ASGITransport(
            app=lambda scope, receive, send: manager.handle_request(
                scope, receive, send))
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            r = await client.post(
                "/mcp", headers=ACCEPT,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": MODERN, "capabilities": {},
                                 "clientInfo": {"name": "era-probe",
                                                "version": "0"}}})
            assert r.status_code == 200, r.text[:200]
            answered = (json.loads(_body_text(r)).get("result") or {}).get(
                "protocolVersion")
            assert answered == LEGACY_LATEST, answered
