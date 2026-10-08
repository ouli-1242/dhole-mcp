"""升级链把"哪一层说了什么"丢了，于是沉默被翻译成「去查你的 DNS」。

现场（2026-10-08 重启后）：宿主里 `web_read(example.com)` 两档全 status 0，回执是
`all_tiers_failed: unknown (HTTP status 0)` + 「Check the URL spelling and DNS first」。
而同一时刻 `dhole --doctor` 刚把 example.com 判成公网可解析，本机直连同一路 8/8 成功 —
"去查 DNS"是把人往已经证明没问题的地方支。

分类器（`errors.classify_network_error`）认 10054/10061/198.18.x/ERR_NAME_NOT_RESOLVED…，
可读的那句话在哪一层？实测在 HTTP 档的 `result.error` 里（一根死代理就打出
`... (os error 10061)`）。但升级链只把 `HTTP failed (status 0)` 写进 `errors`（当年为了
不让原始异常句把 all_tiers_failed 撑爆），而分类吃的是**浏览器档**的 error —— 浏览器档
沉默时便退回报纸一样的状态号串，什么线索也没有，于是所有沉默都归进 `unknown`。

判决：每一档说过的话要带着走（限长），既进分类也进回执；浏览器档自己开口时优先级不变。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.errors import classify_network_error
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

URL = "https://example.com/"

# 真实形状：httpx/primp 在 Windows 上的原话（尾码是 winsock 的）
RESET_WORDS = ("error sending request for url (https://example.com/) "
               "> connection was reset by peer (os error 10054)")
REFUSED_WORDS = ("error sending request for url (https://example.com/) "
                 "> 由于目标计算机积极拒绝，无法连接。 (os error 10061)")
FAKE_IP_WORDS = "connect to 198.18.0.42 failed after 2000ms timeout"


def _tier(fetcher: str, error: str) -> ResponseModel:
    return ResponseModel(url=URL, status=0, content=[], fetcher_used=fetcher,
                         extracted_type="markdown", error=error)


def _chain(http_error: str, browser_error: str, monkeypatch) -> MasterFetchServer:
    """两档各自按测试给的话失败，浏览器二进制算它在场，归档兜底关掉。"""
    server = MasterFetchServer()
    server.get = AsyncMock(return_value=_tier("http", http_error))
    server.stealthy_fetch = AsyncMock(return_value=_tier("stealthy", browser_error))
    # 隐身档之前的"取自动会话"那一步就会起浏览器，它也得 stub 掉
    server._ensure_auto_session = AsyncMock(return_value="auto-test")
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    monkeypatch.setattr(server_mod, "ensure_chromium_async",
                        AsyncMock(return_value=SimpleNamespace(ok=True, waited_s=0,
                                                               detail="")))
    monkeypatch.setattr(server_mod, "_should_try_archive", lambda result: False)
    return server


class TestWhatEachTierSaidTravels:

    @pytest.mark.asyncio
    async def test_a_reset_named_by_http_is_not_reported_as_dns_trouble(self, monkeypatch):
        """HTTP 档说了 10054，浏览器档沉默：分类该听见 10054。"""
        out = await _chain(RESET_WORDS, "", monkeypatch).web_read(URL, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: connection_reset"), out.error
        assert "forcibly closed" in out.error, "建议要跟着类别走"

    @pytest.mark.asyncio
    async def test_a_refused_connection_keeps_its_winsock_code_in_the_receipt(
            self, monkeypatch):
        """分类之外，回执那一行也得带上原话 —— 否则读者只能信我们给的类别。"""
        out = await _chain(REFUSED_WORDS, "", monkeypatch).web_read(URL, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: connection_refused"), out.error
        assert "os error 10061" in out.error

    @pytest.mark.asyncio
    async def test_a_fake_ip_address_in_the_tier_words_is_read_as_the_tunnel(
            self, monkeypatch):
        """这台机器的老问题：198.18.x 是 TUN 没通到真实网络，不是站点 down、更不是 DNS。"""
        out = await _chain(FAKE_IP_WORDS, "", monkeypatch).web_read(URL, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: connection_refused"), out.error
        assert "Check the URL spelling and DNS" not in out.error

    @pytest.mark.asyncio
    async def test_the_browser_tier_still_speaks_for_itself(self, monkeypatch):
        """反向不许漂：浏览器档自己开口时，分类仍以它为准（HTTP 的旧话不许盖过它）。"""
        out = await _chain(RESET_WORDS, "Timed out while launching browser",
                           monkeypatch).web_read(URL, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: browser_launch"), out.error

    @pytest.mark.asyncio
    async def test_genuine_silence_still_reports_reachability(self, monkeypatch):
        """两层都真的什么都没说出时，不发明线索 —— 仍旧走"没有收到任何响应"那条。"""
        out = await _chain("", "", monkeypatch).web_read(URL, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: unknown"), out.error
        assert "No HTTP response was received at all" in out.error
        assert "HTTP failed (status 0); Stealthy failed (status 0)" in out.error


class TestTheDriverSentenceSaidAloud:
    """`errors.py` 的注释点名"驱动的原话是 Timed out while launching browser，必须先于
    timeout 认它"，模式表里却只有带 `browserType.launch:` 前缀的形状 —— 裸的那句仍然落进
    timeout，于是建议变成「把 timeout 调大再试」，正是要避免的那条假出路。`actions.py`
    早就按 `while launching browser` 这个片段认它，两份名单不该有出入。"""

    def test_the_bare_sentence_is_browser_launch(self):
        assert classify_network_error(
            "Timed out while launching browser")[0] == "browser_launch"

    def test_the_prefixed_sentence_still_is(self):
        assert classify_network_error(
            "browserType.launch: Timed out while launching browser")[0] == "browser_launch"
