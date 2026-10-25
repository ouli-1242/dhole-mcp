"""两项模拟发现（2026-10-25 真机）的修复钉。

P2 · `web_request` 收 `session_id`：登录 POST 的 Set-Cookie 要有地方落。jar 的
存取在 fetch 层、每跳每方法都跑（fetcher.py 的 sessions.store 没有 GET 门），
缺的只是这扇门把调用方点名的 jar 递进 web_read——不递，POST 登录成功而后续
读永远拿匿名 jar。真机证据：`review/manual-2026-10-25/probe_cookie_jar.py`
（POST /login 成功、/account 仍是未登录壳）。

P3 · `web_research` 分摊预算 clamp 到 web_read 的上限：首源快时下一源拿到
>120000ms 的 share，被 `security.TIMEOUT_MAX_MS` 的校验**整发拒掉**（真机
dict.cn 那一行的 why）。这里 clamp 而不是拒——预算是"至多等这么多"，减到
上限丢的是余量不是语义。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from dhole_mcp.models import ResponseModel
from dhole_mcp.security import SecurityError, TIMEOUT_MAX_MS
from dhole_mcp.server import MasterFetchServer


# ── P2 · web_request 的 session_id 递进 web_read ─────────────────────────


def _capture_read(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def fake_read(self, **kw):
        seen.append(kw)
        return ResponseModel(url=kw.get("url") or "", status=200,
                             content=["ok"], fetcher_used="http",
                             content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "web_read", fake_read)
    return seen


def test_web_request_forwards_the_session_id_to_web_read(monkeypatch):
    seen = _capture_read(monkeypatch)
    out = asyncio.run(MasterFetchServer().web_request(
        url="https://s0.test/login", method="POST",
        body={"username": "u", "password": "p"}, confirm=True,
        session_id="sim-main"))
    assert not out.error, out.error
    assert seen and seen[0].get("session_id") == "sim-main", (
        "jar 名没递进 web_read —— 写响应的 Set-Cookie 无处可落")


def test_dispatch_does_not_refuse_session_id_on_web_request(monkeypatch):
    """表外名那一族是第一道闸：白名单缺它时，调用方在 web_request 执行之前就被
    "Unsupported argument(s)" 拒掉（真机 [14] 的原形状）。"""
    seen = _capture_read(monkeypatch)
    server = MasterFetchServer()
    out = asyncio.run(server._dispatch(
        "web_request", {"url": "https://s0.test/login",
                        "method": "POST", "confirm": True,
                        "session_id": "sim-main"}))
    assert "Unsupported argument" not in str(out), out
    assert seen and seen[0].get("session_id") == "sim-main"


def test_the_wire_schema_advertises_session_id_on_web_request():
    """调用方只读 tools/list：这颗参数必须在 schema 上，否则它永远不知道
    登录 POST 有落点（test_every_accepted_name_is_advertised 钉的是两侧一致，
    这里钉的是"这一侧真的在场"）。"""
    td = next(t for t in MasterFetchServer._TOOL_DEFS
              if t["name"] == "web_request")
    prop = td["inputSchema"]["properties"].get("session_id")
    assert prop is not None and "jar" in (prop.get("description") or "")


# ── P3 · research 的分摊预算 clamp ────────────────────────────────────────


def _wire_with_read_contract(monkeypatch, seen: list[dict], rows=2):
    """假搜索 + 遵守真 web_read 合同的假读取：timeout 超过
    `security.TIMEOUT_MAX_MS` 就抛出与真校验器同文的错误——没有 clamp 时
    第二源在这一步被拒，包里多一行 failed。"""

    async def fake_search(self, query="", **kw):
        return SimpleNamespace(
            results=[SimpleNamespace(url=f"https://s{i}.test/a", title=f"t{i}")
                     for i in range(rows)],
            error="", next_action="")

    async def fake_read(self, url, focus=None, max_content_chars=4000,
                        timeout=None, **kw):
        seen.append({"url": url, "timeout": timeout})
        if timeout is not None and int(timeout) > TIMEOUT_MAX_MS:
            raise SecurityError(
                f"Timeout exceeds maximum of {TIMEOUT_MAX_MS}ms (2 minutes)")
        return ResponseModel(url=url, status=200,
                             content=["一段正常的证据正文，带句号。" * 40],
                             fetcher_used="http", content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "web_search", fake_search)
    monkeypatch.setattr(MasterFetchServer, "web_read", fake_read)


def test_a_fast_first_source_does_not_starve_the_second_with_an_oversized_timeout(monkeypatch):
    """timeout=150000、首源即刻返回：第二源的分摊份额会超过 web_read 的
    120000 上限。修复前它被整发拒（failed 里多一行 Timeout exceeds）；
    修复后被 clamp 到上限、照常成包。"""
    seen: list[dict] = []
    _wire_with_read_contract(monkeypatch, seen)
    out = asyncio.run(MasterFetchServer().web_research(
        query="q", max_sources=2, timeout=150_000))
    assert out["failed"] == [], out["failed"]
    assert len(out["evidence"]) == 2
    assert seen and len(seen) == 2
    for row in seen:
        assert 5_000 <= row["timeout"] <= TIMEOUT_MAX_MS, row


def test_a_burned_budget_still_sends_the_next_read_with_a_legal_floor(monkeypatch):
    """反向：预算被首源烧掉大半时，第二源的份额贴着下限 5s 仍要发读——
    clamp 的下限保证它带着一个合法的正数，而不是 0 或负数去撞校验。"""
    import dhole_mcp.server as server_mod

    seen: list[dict] = []
    _wire_with_read_contract(monkeypatch, seen)
    clock = {"t": 0.0}

    def fake_now():
        return clock["t"]

    async def slow_read(self, url, focus=None, max_content_chars=4000,
                        timeout=None, **kw):
        seen.append({"url": url, "timeout": timeout})
        clock["t"] += 55.0
        if timeout is not None and int(timeout) > TIMEOUT_MAX_MS:
            raise SecurityError(
                f"Timeout exceeds maximum of {TIMEOUT_MAX_MS}ms (2 minutes)")
        return ResponseModel(url=url, status=200,
                             content=["一段正常的证据正文，带句号。" * 40],
                             fetcher_used="http", content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "web_read", slow_read)
    monkeypatch.setattr(server_mod, "now", fake_now)
    out = asyncio.run(MasterFetchServer().web_research(
        query="q", max_sources=2, timeout=60_000))
    assert len(seen) == 2, out
    assert seen[1]["timeout"] is not None and seen[1]["timeout"] >= 5_000, seen[1]
    assert out["failed"] == [], out["failed"]
