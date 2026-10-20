"""17.0 B2：搜索出口只剩"一个变量一个代理"。

原来这一层是代理**池**：``~/.dhole/search_proxies.json`` + round-robin 轮转 + 按失败
冷却 + ``dhole proxy add/remove/clear``。池子整体删了，本文件守的是活下来的那部分，
以及"删干净了"这件事本身：

  * 解析顺序 DHOLE_SEARCH_PROXY → HTTPS_PROXY → HTTP_PROXY → ALL_PROXY 一个都不能少
    （后三个是环境里已经有的东西；沙箱与 CI 常常只设它们，漏掉就等于这台机器突然
    变成直连，而没人会怀疑一个"我没设过"的变量）；
  * 方案不认识就当没设，并留下话 —— 把笔误递到下游的结果是每次搜索都失败且报错
    指向代理本身；
  * 逗号分隔的老写法仍能用，但只有第一个生效，并且必须说出来（静默少用 N-1 个
    出口等于没交代）；
  * 任何把代理 URL 说出去的地方都得先抹掉 userinfo。
"""

import json
import logging

import pytest

from dhole_mcp import env_registry, search_proxy
from dhole_mcp.search_proxy import configured_proxy, redact

_ALL_VARS = ("DHOLE_SEARCH_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")


@pytest.fixture(autouse=True)
def _no_proxy_in_the_environment(monkeypatch):
    """这台开发机很可能本来就挂着代理（HTTPS_PROXY 之类）—— 解析顺序的每条断言
    都要求起点干净，否则测的是本机网络而不是这段代码。"""
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


# ─── 解析顺序 ────────────────────────────────────────────────────────────

class TestResolutionOrder:

    def test_nothing_configured_is_direct(self):
        assert configured_proxy() == (None, "")

    def test_dhole_var_wins_over_the_generic_ones(self, monkeypatch):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "http://dhole-egress:8080")
        monkeypatch.setenv("HTTPS_PROXY", "http://system-egress:8080")
        proxy, source = configured_proxy()
        assert proxy == "http://dhole-egress:8080"
        assert source == "DHOLE_SEARCH_PROXY"

    @pytest.mark.parametrize("name", ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"))
    def test_the_generic_variables_are_still_honoured(self, monkeypatch, name):
        """兜底链不是礼貌，是功能：只设了 HTTPS_PROXY 的机器换到 dhole 之后仍该走它。"""
        monkeypatch.setenv(name, "http://system-egress:8080")
        proxy, source = configured_proxy()
        assert proxy == "http://system-egress:8080"
        assert source == name

    def test_fallback_order_is_https_then_http_then_all(self, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://lower:1")
        monkeypatch.setenv("ALL_PROXY", "http://lowest:2")
        assert configured_proxy() == ("http://lower:1", "HTTP_PROXY")
        monkeypatch.setenv("HTTPS_PROXY", "http://top:3")
        assert configured_proxy() == ("http://top:3", "HTTPS_PROXY")

    def test_the_config_file_door_works_too(self, monkeypatch):
        """DHOLE_SEARCH_PROXY 是可持久化旋钮，所以它必须能被 `dhole config set` 喂。

        只读 os.environ 的实现会做出两扇门长得一样、只有一扇有用的东西 ——
        正是本批次要消灭的那类缺陷。
        """
        seen = {}

        def fake_value_of(name):
            seen["name"] = name
            return "http://from-config:8080"

        monkeypatch.setattr(env_registry, "value_of", fake_value_of)
        proxy, source = configured_proxy()
        assert proxy == "http://from-config:8080"
        assert seen["name"] == "DHOLE_SEARCH_PROXY"

    def test_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "   http://1.2.3.4:8080  ")
        assert configured_proxy()[0] == "http://1.2.3.4:8080"


# ─── 方案校验：宁可当没设，也不要把笔误递下去 ──────────────────────────

class TestSchemeValidation:

    @pytest.mark.parametrize("url", [
        "http://1.2.3.4:8080", "https://1.2.3.4:443",
        "socks5://1.2.3.4:1080", "socks5h://1.2.3.4:1080",
        "http://user:pass@1.2.3.4:8080",
    ])
    def test_accepted_schemes(self, monkeypatch, url):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", url)
        assert configured_proxy()[0] == url

    @pytest.mark.parametrize("bad", ["ftp://1.2.3.4:21", "1.2.3.4:8080", "://x"])
    def test_rejected_schemes_are_reported_as_unset(self, monkeypatch, caplog, bad):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", bad)
        with caplog.at_level(logging.WARNING, logger="dhole-mcp.search_proxy"):
            proxy, source = configured_proxy()
        assert proxy is None
        assert source == "DHOLE_SEARCH_PROXY"
        assert "unsupported scheme" in caplog.text

    def test_a_blank_value_is_not_a_proxy(self, monkeypatch):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "   ")
        monkeypatch.setenv("HTTPS_PROXY", "http://real:80")
        # 空的 dhole 变量不许把兜底链挡在门外
        assert configured_proxy() == ("http://real:80", "HTTPS_PROXY")


# ─── 逗号分隔：老写法仍读得通，但只取第一个并且说出来 ────────────────────

class TestNoRotationAnymore:

    def test_first_of_a_list_wins_and_the_silence_is_reported(self, monkeypatch, caplog):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "http://p1:80,socks5://p2:1080,http://p3:80")
        with caplog.at_level(logging.WARNING, logger="dhole-mcp.search_proxy"):
            proxy, _source = configured_proxy()
        assert proxy == "http://p1:80"
        # R02-F49：句子去掉「17.0」，守的三件事一件不少 —— 池子不存在、什么都不轮换、
        # 只用第一颗。断言跟着换成新的字面，标准没放松（三句都要在）。
        assert "no pool" in caplog.text, caplog.text
        assert "nothing rotates" in caplog.text, caplog.text
        assert "first one is used" in caplog.text, caplog.text

    def test_repeated_calls_give_the_same_egress(self, monkeypatch):
        """轮转没了：同一进程里每次搜索走同一个出口。这条断言是"没有隐藏状态"的
        证据 —— 曾经的池子在两次调用之间是有记忆的。"""
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "http://p1:80,http://p2:80")
        assert [configured_proxy()[0] for _ in range(4)] == ["http://p1:80"] * 4

    def test_the_first_entry_is_validated_not_trusted(self, monkeypatch):
        """列表里第一个是坏方案时，不许"跳过它用下一个" —— 那是把删除的轮转换个
        名字偷偷装回来。"""
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "ftp://bad:21,http://good:80")
        assert configured_proxy()[0] is None


# ─── 脱敏：代理 URL 里的 userinfo 是凭据 ────────────────────────────────

class TestRedaction:

    def test_username_and_password_are_masked(self):
        out = redact("http://alice:s3cret@corp-egress.example:3128")
        assert out == "http://***@corp-egress.example:3128"
        assert "alice" not in out and "s3cret" not in out

    def test_username_only_gets_the_same_mask(self):
        """定长 *** 不泄露"有没有密码"以及用户名长度。"""
        assert redact("http://alice@corp:3128") == "http://***@corp:3128"

    def test_a_proxy_without_credentials_passes_through(self):
        assert redact("http://1.2.3.4:8080") == "http://1.2.3.4:8080"

    def test_empty_and_none_are_empty_strings(self):
        assert redact("") == "" and redact(None) == ""

    def test_a_broken_port_does_not_raise(self):
        """脱敏发生在报错路径上，它自己不能成为第二个故障。"""
        assert "http://" in redact("http://u:p@host:notaport")


# ─── metasearch 侧的接线 ────────────────────────────────────────────────

class TestMetasearchWiring:

    def test_unconfigured_is_direct(self):
        from dhole_mcp.search_metasearch import _get_search_proxy
        assert _get_search_proxy() is None

    def test_the_module_level_proxy_tracks_the_call(self, monkeypatch):
        """find_similar 那条路读的是模块量而不是参数，所以它必须与本次出口一致 ——
        留一个陈旧值会让它从另一个 IP 出去，而没人知道。"""
        import dhole_mcp.search_metasearch as sm

        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "http://p1:80")
        sm._PROXY = "stale_value"
        try:
            assert sm._get_search_proxy() == "http://p1:80"
            assert sm._PROXY == "http://p1:80"
            assert sm._get_search_proxy() == "http://p1:80"   # 第二次不会换一个
        finally:
            sm._PROXY = None

    def test_metasearch_names_the_egress_in_the_failure_without_leaking_it(
            self, monkeypatch):
        """"所有引擎都起不来"的报错要说清走了哪个出口（否则人只会去改查询），
        但不能把 userinfo 交出去 —— 这条 message 会进 agent 视野与 usage 审计。"""
        import asyncio

        from dhole_mcp import search_metasearch as sm

        class _WontStart:
            """让"起不来"这件事由测试决定，而不是由当场的网络决定。
            引擎构造失败是那条报错的唯一入口，走的是真实代码路径。"""
            disabled = False

            def __init__(self, **kwargs):
                raise RuntimeError("connection refused")

        monkeypatch.setitem(sm._TEXT_ENGINES, "baidu", _WontStart)
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "http://alice:s3cret@corp:3128")
        with pytest.raises(sm.MetaSearchException) as ei:
            asyncio.run(sm.metasearch("query", 3, engines=["baidu"]))
        msg = str(ei.value)
        assert "corp" in msg, msg          # 出口说的是哪台
        assert "s3cret" not in msg and "alice" not in msg, msg


# ─── 池子确实不在了（防复活的守卫） ─────────────────────────────────────

class TestPoolIsGone:

    @pytest.mark.parametrize("name", [
        "ProxyPool", "get_proxy_pool", "get_next_proxy", "reset_pool",
        "load_proxies", "save_proxies", "add_proxy", "remove_proxy",
        "clear_proxies", "list_proxies", "_config_path", "_kick_health_check",
        "_read_config_file", "_read_env_var", "_env_proxy_source", "_redact",
        "_validate_proxy", "MAX_PROXIES",
    ])
    def test_the_module_exposes_no_pool_api(self, name):
        assert not hasattr(search_proxy, name), f"代理池的 {name} 又回来了"

    def test_a_search_proxies_json_is_ignored(self, monkeypatch, tmp_path):
        """老状态文件不再是一个来源：把 16.x 形状的文件放在 DHOLE_HOME 里，解析结果
        仍然是"没设"。否则就多了一条没人知道、还明文存着一批凭据的入口。"""
        (tmp_path / "search_proxies.json").write_text(
            json.dumps({"proxies": ["http://ghost:80"]}), encoding="utf-8")
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        assert configured_proxy() == (None, "")

    def test_crawl_and_metasearch_import_only_the_resolver(self):
        """调用面收窄成一个函数：谁再 import 池子的 API，这条就红。"""
        import inspect

        from dhole_mcp import crawl, search_metasearch

        for module in (crawl, search_metasearch):
            src = inspect.getsource(module)
            for gone in ("get_next_proxy", "get_proxy_pool", "_kick_health_check"):
                assert gone not in src, f"{module.__name__} 又用上了 {gone}"
