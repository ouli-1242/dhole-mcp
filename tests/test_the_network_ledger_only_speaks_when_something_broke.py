"""网络账本只在**有东西没成**的时候说话。

四个缺陷的教训在这里收口：一份"每次都有"的诊断账是噪声（实测 163 正文页实际发出
55 个请求、失败 0），而它唯一有用的时刻是页面**没说清为什么**的时候 —— js_shell 的
正文缺口背后常常是一个 403 的数据接口。

这组用例锁住四件事：
  1. 干净页一个字节都不付（`network` 缺席）；
  2. 有失败时才上线，且说的是那条请求（状态 + 类型 + 脱敏后的 URL）；
  3. 查询串的值永远不进输出（凭据最常见的泄漏面）；
  4. 缓存不携带它 —— 那一发一个请求都没发，带上一份就是假话。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.browser import _mask_url, _net_payload, _note_net_response
from dhole_mcp.models import ResponseModel
from dhole_mcp.wire import _wire_dump


# ─── 夹具 ────────────────────────────────────────────────────────────────────

def _resp(resource_type: str, status: int, url: str):
    """一条够 `_note_net_response` 读的响应（只带它真读的那两格）。"""
    req = SimpleNamespace(resource_type=resource_type,
                          is_navigation_request=lambda: resource_type == "document")
    return SimpleNamespace(request=req, status=status, url=url)


def _watch() -> dict:
    return {"requests": 0, "failed": []}


# ─── 脱敏 ────────────────────────────────────────────────────────────────────

def test_a_query_string_value_never_reaches_the_wire():
    """凭据最常见的泄漏面是查询串；键名留着才有诊断价值。"""
    masked = _mask_url("https://api.test/feed?token=s3cret&page=2")
    assert "s3cret" not in masked
    assert "token=***" in masked
    assert "page=***" in masked


def test_a_key_without_a_value_is_kept_as_is():
    assert _mask_url("https://api.test/x?debug&b=1") == "https://api.test/x?debug&b=***"


def test_the_fragment_is_dropped_because_it_was_never_sent():
    assert _mask_url("https://api.test/a?b=1#section") == "https://api.test/a?b=***"


# ─── 计数口径 ────────────────────────────────────────────────────────────────

def test_assets_dhole_blocks_itself_are_not_counted_as_page_requests():
    """图片/样式表被 route abort，它们不是"页面发出的请求"。"""
    w = _watch()
    _note_net_response(w, _resp("image", 200, "https://a/i.png"))
    _note_net_response(w, _resp("stylesheet", 200, "https://a/s.css"))
    _note_net_response(w, _resp("script", 200, "https://a/x.js"))
    assert w["requests"] == 1


def test_the_main_document_4xx_is_not_repeated_in_the_ledger():
    """主文档的状态已经是 `result.status`，在信封里再说一遍只是噪声。"""
    w = _watch()
    _note_net_response(w, _resp("document", 404, "https://a/missing"))
    assert w["requests"] == 1
    assert w["failed"] == []


def test_a_failing_subresource_is_recorded_with_its_status_and_type():
    w = _watch()
    _note_net_response(w, _resp("xhr", 403, "https://api.test/feed?token=zzz"))
    assert w["failed"] == [{"status": 403, "type": "xhr",
                            "url": "https://api.test/feed?token=***"}]


def test_the_failed_list_is_capped_so_a_broken_page_cannot_flood_the_wire():
    w = _watch()
    for i in range(40):
        _note_net_response(w, _resp("fetch", 500, f"https://api.test/{i}"))
    assert len(w["failed"]) == 10


# ─── 上线与否 ────────────────────────────────────────────────────────────────

def test_a_clean_page_pays_nothing():
    """实测 163 正文页 55 个请求、0 失败 —— 这份账没有话要说。"""
    w = _watch()
    _note_net_response(w, _resp("script", 200, "https://a/x.js"))
    assert w["requests"] == 1
    assert _net_payload(w) is None


def test_a_failure_makes_the_ledger_speak():
    w = _watch()
    _note_net_response(w, _resp("script", 200, "https://a/x.js"))
    _note_net_response(w, _resp("xhr", 403, "https://api.test/feed?k=v"))
    payload = _net_payload(w)
    assert payload["requests"] == 2
    assert payload["failed"][0]["status"] == 403


def test_a_guard_refusal_is_a_security_event_not_a_site_error():
    """SSRF / 名册拦下的主机从 `ssrf_watch` 派生，不另抄一份。"""
    w = _watch()
    _note_net_response(w, _resp("script", 200, "https://a/x.js"))
    payload = _net_payload(w, [("192.168.1.1", False), ("10.0.0.1", False)])
    assert payload["blocked_by_guard"] == ["10.0.0.1", "192.168.1.1"]


# ─── 抄进信封 ────────────────────────────────────────────────────────────────

def test_the_browser_page_hands_its_ledger_to_the_response_model(monkeypatch):
    ledger = {"requests": 58, "failed": [
        {"status": 403, "type": "xhr", "url": "https://api.test/feed?token=***"}]}
    page = SimpleNamespace(headers={}, network=ledger)
    monkeypatch.setattr(server_mod, "_translate_response_body",
                        lambda *a, **k: ResponseModel(status=200, content=["x"],
                                                      url="https://a/"))
    out = server_mod._translate_response(page, "markdown", None)
    assert out.network == ledger


def test_an_http_tier_page_has_no_ledger_to_hand_over(monkeypatch):
    """HTTP 档没有浏览器，也就没有网络账 —— 缺席，不是空对象。"""
    page = SimpleNamespace(headers={})  # 没有 network 这一格
    monkeypatch.setattr(server_mod, "_translate_response_body",
                        lambda *a, **k: ResponseModel(status=200, content=["x"],
                                                      url="https://a/"))
    out = server_mod._translate_response(page, "markdown", None)
    assert out.network is None


# ─── 线格式 ──────────────────────────────────────────────────────────────────

def test_a_clean_response_does_not_pay_for_the_ledger_on_the_wire():
    clean = ResponseModel(status=200, content=["x"], url="https://a/")
    assert "network" not in _wire_dump(clean)


def test_a_failed_ledger_rides_the_wire():
    dirty = ResponseModel(status=200, content=["x"], url="https://a/",
                          network={"requests": 58, "failed": [
                              {"status": 403, "type": "xhr", "url": "u"}]})
    assert _wire_dump(dirty)["network"]["requests"] == 58


@pytest.mark.parametrize("field", ["network"])
def test_the_cache_envelope_never_carries_the_ledger(field):
    """缓存写的是"页面是什么"，不是"这一发网络里发生了什么"。

    写入点在 server 的 `set_cached(envelope={...})`，那里逐字段列举。这条用例钉的是
    那个列表本身：把 `network` 加进去就等于让下一次缓存命中报一份没有发生过的网络账。
    """
    import inspect
    src = inspect.getsource(server_mod)
    start = src.index("await set_cached(")
    block = src[start:start + 2000]
    assert field not in block, "the cache envelope must not carry the network ledger"


# ─── 让判决说出原因（1b） ────────────────────────────────────────────────────

_PROSE = ("这是一段正常的正文，它有句号，也有逗号，读起来像人写的。"
          "它足够长，长到不会被当成菜单碎片。") * 12


def _shell(**extra) -> ResponseModel:
    """一张浏览器档交回的壳页：渲染过了，正文还是空的。"""
    return server_mod._with_agent_hints(ResponseModel(
        url="https://portal.test/app", status=200, content=["<!DOCTYPE html>"],
        fetcher_used="stealthy", total_size_bytes=4_300,
        error="js_shell_detected: the page is a JavaScript shell", **extra))


def test_a_shell_names_the_endpoint_that_failed():
    """从"这页需要 JS"升级成"它的数据接口 403"—— 前者只会让人重试一次。"""
    out = _shell(network={"requests": 40, "failed": [
        {"status": 403, "type": "xhr", "url": "https://api.test/feed?token=***"}]})
    assert "network log says why" in out.next_action, out.next_action
    assert "403" in out.next_action
    assert "api.test/feed" in out.next_action


def test_a_shell_without_a_ledger_says_nothing_extra():
    """HTTP 档没有浏览器，也就没有账 —— 缺席时一个字都不许加。"""
    out = _shell()
    assert "network log" not in out.next_action


def test_a_guard_refusal_is_reported_as_a_boundary_not_a_site_error():
    out = _shell(network={"requests": 3, "blocked_by_guard": ["192.168.1.1"]})
    assert "security boundary" in out.next_action
    assert "do not route around it" in out.next_action


def test_a_page_that_delivered_its_content_does_not_get_a_network_lecture():
    """正文已经拿到的一发不需要一份"某个图标 404"的账 —— 那是噪声。"""
    out = server_mod._with_agent_hints(ResponseModel(
        url="https://portal.test/a", status=200, content=[_PROSE],
        fetcher_used="stealthy", network={"requests": 40, "failed": [
            {"status": 404, "type": "image", "url": "https://a/i.png"}]}))
    assert out.content_ok is True
    assert "network log" not in out.next_action
