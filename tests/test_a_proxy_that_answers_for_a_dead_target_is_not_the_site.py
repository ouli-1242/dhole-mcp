# -*- coding: utf-8 -*-
"""R01-041 · 代理代答的 502 不许写成「源站挂了，而且本次请求无责任」。

现场（P10 罗沐，凌晨值班，`sessions/P10-s8-dns.json` / `P10-s8b-dns2.json`）：

    web_read("http://no-such-host-oztq7.invalid/")  → status=502, 0B, 32ms
    web_read("http://qx7b-another-nonexistent.invalid/") → status=502, 0B, 64ms
    next_action: "the server failed at the server (HTTP 502). Nothing in this request
                  caused it and no parameter fixes it. … the site is down …"

`.invalid` 是 RFC 6761 保留、**保证不解析**的名字 —— 源站从来没有说过任何话。
回答的是这台机器上的代理（本机注册表系统代理 127.0.0.1:7890，fake-ip TUN 在场时
连不存在的名字也会拿到一个 198.18.x 地址并代答）。区分实验（P3 做过、本刀复做）：

    trust_env=False 直连  → primp: RemoteProtocolError（那边根本没有服务器在听）
    trust_env=True 走代理 → 502 / 0B —— 与 dhole 所见一模一样

所以「取不到」是 C（环境），**把代理的代答写成源站的自述**是 B：它把 SRE 的排障方向
指反（他据此去开「站点不可用」的工单），而回执自己手里就有反证（0B + 32ms +
一个不可能解析的名字）。修的是归属，不是政策：5xx 仍然不升级浏览器、仍然不可重试，
只是不许在证据指向自家代理时替源站说话。
"""

from __future__ import annotations

import pytest

from dhole_mcp import fetcher
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _agent_hints


def _result(*, status: int = 502, url: str = "https://no-such-host-oztq7.invalid/",
            content=None, size: int = 0, duration_ms: int = 32,
            error: str = "http_error_502: server returned error status") -> ResponseModel:
    return ResponseModel(status=status, content=content if content is not None else [],
                         url=url, fetcher_used="http", content_type="text/html",
                         total_size_bytes=size, duration_ms=duration_ms, error=error)


def _hints(result) -> str:
    """next_action is the second of the three hints."""
    return _agent_hints(result)[1]


def _clear_proxy_env(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                 "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)


# ─── 1 · 谁在这条路上 ───────────────────────────────────────────────────────

def test_the_system_proxy_is_read_from_the_places_the_tiers_inherit(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    assert "127.0.0.1:7890" in fetcher.system_proxy_for(
        "http://no-such-host-oztq7.invalid/")


def test_a_target_exempted_by_no_proxy_dials_direct(monkeypatch):
    """G31 把回环永久排除出代理。对那样的目标，502 就是源站说的，不许赖代理。"""
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")
    assert fetcher.system_proxy_for("http://127.0.0.1:8399/x") == ""
    assert fetcher.system_proxy_for("http://elsewhere.test/x") != ""


def _no_system_proxy_at_all(monkeypatch):
    """这台机器有注册表系统代理（`getproxies()` 会读到它），所以「完全没有代理」
    只能把读代理那一步本身钉住 —— 不是改判据，是把环境做成用例要说的那 world。"""
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "getproxies_environment", lambda: {})
    monkeypatch.setattr(_ur, "getproxies_registry", lambda: {}, raising=False)


def test_no_proxy_configured_means_no_intermediary_story(monkeypatch):
    _clear_proxy_env(monkeypatch)
    _no_system_proxy_at_all(monkeypatch)
    assert fetcher.system_proxy_for("https://example.com/") == ""


# ─── 2 · 那句话的两种真话 ───────────────────────────────────────────────────

def test_a_zero_byte_502_behind_a_proxy_stops_blaming_the_site(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    text = _hints(_result())
    assert "Nothing in this request caused it" not in text, text
    assert "the site is down" not in text, text
    assert "proxy" in text.lower(), text
    assert "no bytes" in text.lower() or "0B" in text, text


def test_the_proxy_story_names_the_measured_shape_not_a_guess(monkeypatch):
    """归属必须带着它据以归属的证据（状态、字节数、耗时、谁在台上），
    否则只是把另一句无据断言换了个方向。"""
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    text = _hints(_result(status=503, duration_ms=64))
    assert "503" in text and "64" in text, text


def test_a_registry_proxy_is_still_seen_when_only_no_proxy_is_in_the_env(monkeypatch):
    """这条是活体复验抓出来的，不是纸上想出来的。

    G31 会把 `localhost,127.0.0.1,::1` 并进 NO_PROXY —— 而 `urllib.getproxies()` 的实现是
    「env 里非空就只用 env」，`no_proxy` 本身会被解析成一颗键名 `no` 的“代理”，于是
    **注册表里那台系统代理再也不被读到**：dhole 的进程里 `getproxies()` 稳定是
    `{'no': 'localhost,127.0.0.1,::1'}`。任何只看 env 的写法都会在这台机器上说
    「没有代理在台」，而那正是本缺陷的反面。"""
    import urllib.request as _ur
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")
    monkeypatch.setattr(_ur, "getproxies_environment",
                        lambda: {"no": "localhost,127.0.0.1,::1"})
    monkeypatch.setattr(_ur, "getproxies_registry",
                        lambda: {"http": "http://127.0.0.1:7890",
                                 "https": "http://127.0.0.1:7890"}, raising=False)
    assert fetcher.system_proxy_for("http://abc.invalid/") == "http://127.0.0.1:7890"
    # 被 NO_PROXY  exempted 的目标仍然算直连（回环那条排除是无条件的）
    assert fetcher.system_proxy_for("http://127.0.0.1:8399/x") == ""


def test_a_live_empty_502_on_an_unresolvable_name_does_not_blame_the_site(monkeypatch):
    """整条链路：句子由 `_agent_hints` 说，代理由这台机器的真实配置决定。"""
    import urllib.request as _ur
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")
    monkeypatch.setattr(_ur, "getproxies_environment",
                        lambda: {"no": "localhost,127.0.0.1,::1"})
    monkeypatch.setattr(_ur, "getproxies_registry",
                        lambda: {"http": "http://127.0.0.1:7890"}, raising=False)
    text = _hints(_result())
    assert "the site is down" not in text and "proxy" in text.lower(), text


def test_a_5xx_that_came_back_with_a_body_is_still_the_server_failing(monkeypatch):
    """不许修成「什么都是代理」：源站带着自己的错误页答复时，旧句子仍然是对的。"""
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("NO_PROXY", "example.com")
    text = _hints(_result(status=500, url="https://example.com/x",
                            content=["Internal Server Error"],
                            size=512, duration_ms=900))
    assert "the server failed at the server" in text, text
    assert "A 5xx is not a bot block." in text, text


def test_a_loopback_502_is_not_pinned_on_the_proxy(monkeypatch):
    """回环目标由 G31 保证直连 —— 那句「代理到不了你的回环」在这里是假话。"""
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    fetcher._ensure_loopback_no_proxy()
    text = _hints(_result(url="http://127.0.0.1:8399/x"))
    assert "the server failed at the server" in text, text


def test_an_empty_body_without_a_proxy_keeps_the_old_sentence(monkeypatch):
    """两个证据缺一个就不许改口：0B 但没有代理在台，仍然只能说「服务器回了 5xx」。"""
    _clear_proxy_env(monkeypatch)
    _no_system_proxy_at_all(monkeypatch)
    text = _hints(_result())
    assert "the server failed at the server" in text, text


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_the_switch_is_about_who_answered_not_about_the_code(monkeypatch, status):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    text = _hints(_result(status=status))
    assert "proxy" in text.lower(), (status, text)
    assert str(status) in text
