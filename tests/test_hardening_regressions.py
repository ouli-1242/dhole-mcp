"""回归测试：日志凭据脱敏 + 代理健康探测的未 await 协程。

三个缺陷都来自「看起来无害、实际有副作用」的写法：

1. ``fetcher.py`` 在重试告警里直接写入原始异常文本。primp/httpx 的异常会
   带上完整代理 URL（形如 ``http://user:pass@host``），于是代理凭据落进
   日志。项目里已有 ``security.redact_api_key``，那里此前漏用。
2. ``search_proxy._kick_health_check`` 把 ``asyncio.create_task(pool.health_check())``
   写在一行：协程对象先被求值，若 ``create_task`` 因「无运行中的事件循环」
   抛 RuntimeError，那个协程已被创建却从未 await，触发
   ``coroutine 'ProxyPool.health_check' was never awaited``。
3. ``search_metasearch._brightdata_serp_search`` 把失败响应体原样写进 debug
   日志。注意 ``redact_api_key`` 的正则只认 ``sk-tinyfish-`` / ``sk|pk|api_key``
   前缀和带凭据的代理 URL，**盖不住 Bright Data 自己的 key 形状**，所以这里
   用的是「拿已知 key 做定向替换」，与格式无关。
"""

from __future__ import annotations

import gc
import logging
import warnings

import pytest

from dhole_mcp.fetcher import HTTPSession


class _CredentialLeakingClient:
    """模拟 primp：异常文本里带出完整的带凭据代理 URL。"""

    def get(self, *args, **kwargs):
        raise RuntimeError(
            "connection failed via http://alice:s3cret@proxy.test:8080 "
            "(proxy rejected)"
        )


@pytest.mark.asyncio
async def test_retry_warning_redacts_proxy_credentials(caplog):
    """重试告警不得把 user:pass@ 形式的代理凭据写进日志。"""
    session = HTTPSession(stealthy_headers=False, retries=1, retry_delay=0)
    session._client = _CredentialLeakingClient()

    with caplog.at_level(logging.WARNING, logger="dhole_mcp.fetcher"):
        with pytest.raises(RuntimeError):
            await session.get("https://example.com/", retries=1)

    assert "s3cret" not in caplog.text, "代理密码泄漏到了日志"
    assert "alice:" not in caplog.text, "代理用户名密码片段泄漏到了日志"
    assert "CREDENTIALS_REDACTED" in caplog.text, "未走 redact_api_key 脱敏"


@pytest.mark.asyncio
async def test_retry_warning_still_mentions_the_url_and_attempt(caplog):
    """脱敏不能把告警本身抹掉——排障信息要保留。"""
    session = HTTPSession(stealthy_headers=False, retries=1, retry_delay=0)
    session._client = _CredentialLeakingClient()

    with caplog.at_level(logging.WARNING, logger="dhole_mcp.fetcher"):
        with pytest.raises(RuntimeError):
            await session.get("https://example.com/page", retries=1)

    assert "https://example.com/page" in caplog.text
    assert "attempt 1" in caplog.text


class _RecordingPool:
    def __init__(self) -> None:
        self.health_check_calls = 0

    async def health_check(self):
        self.health_check_calls += 1


class TestKickHealthCheckWithoutEventLoop:
    """``_kick_health_check`` 在无事件循环时必须是「什么都没发生」。"""

    def test_pool_is_not_touched_without_event_loop(self, monkeypatch):
        """必须先判断事件循环再创建协程，否则会留下未 await 的协程对象。"""
        from dhole_mcp import search_proxy

        touched: list[int] = []
        monkeypatch.setattr(search_proxy, "get_proxy_pool", lambda: touched.append(1))
        monkeypatch.setattr(search_proxy, "_health_task", None)

        search_proxy._kick_health_check()

        assert touched == [], "无事件循环时应立即返回，不应创建协程/触碰代理池"
        assert search_proxy._health_task is None

    def test_no_unawaited_coroutine_warning(self, monkeypatch):
        """直接复现历史症状：不应产生 RuntimeWarning。"""
        from dhole_mcp import search_proxy

        pool = _RecordingPool()
        monkeypatch.setattr(search_proxy, "get_proxy_pool", lambda: pool)
        monkeypatch.setattr(search_proxy, "_health_task", None)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            search_proxy._kick_health_check()
            gc.collect()

        leaked = [w for w in caught if issubclass(w.category, RuntimeWarning)]
        assert not leaked, f"产生了未 await 的协程警告: {[str(w.message) for w in leaked]}"
        assert pool.health_check_calls == 0


_BRIGHTDATA_KEY = "brd-customer-0123-zone-dhole-SECRETDONOTLOG"

class TestBrightDataErrorBodyRedaction:
    """Bright Data 的失败响应体不得把 key 原样写进日志。"""

    @pytest.mark.asyncio
    async def test_key_in_error_body_does_not_reach_log(self, monkeypatch, caplog):
        import httpx
        import logging

        from dhole_mcp import search_metasearch as m

        class _ServerError:
            status_code = 500
            text = f'{{"error":"unauthorized, key {_BRIGHTDATA_KEY} rejected"}}'

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", _BRIGHTDATA_KEY)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _ServerError())

        with caplog.at_level(logging.DEBUG, logger="dhole_mcp.search_metasearch"):
            results, _status = await m.metasearch("q", 3, engines=["brightdata"])

        assert results == []
        assert _BRIGHTDATA_KEY not in caplog.text, "Bright Data key 泄漏到了日志"
        assert "500" in caplog.text, "脱敏不能把状态码这个排障线索一起抹掉"


# ---------------------------------------------------------------------------
# 缓存的作用域：共享状态不得跨请求上下文回放
# ---------------------------------------------------------------------------


class TestCacheContextIsolation:
    """缓存键必须覆盖「改变返回内容」的请求上下文。

    缓存是整机共享的（``~/.dhole/cache.db``），而键里只有
    URL + 抽取参数：带 cookies/auth 抓来的正文会被之后一次匿名抓取原样
    回放，且回放时 ``cached=True`` / ``content_ok=true``。同一份代码为
    ``actions`` 做了「绕过缓存」，却没为凭据做同样处理。
    """

    def test_plain_request_keeps_the_old_empty_fingerprint(self):
        """默认请求指纹为空 —— 修复不能把已有缓存一次性作废。"""
        from dhole_mcp.server import _cache_context

        assert _cache_context({}) == ""
        assert _cache_context({
            "cookies": None, "extra_headers": None, "useragent": None, "proxy": None,
            "main_content_only": True, "use_trafilatura": True,
            "include_media": False, "include_links": False,
        }) == ""

    def test_credentials_and_content_flags_change_the_fingerprint(self):
        from dhole_mcp.server import _cache_context

        base = _cache_context({})
        cookies = _cache_context({"cookies": [{"name": "session", "value": "abc"}]})
        headers = _cache_context({"extra_headers": {"Authorization": "Bearer x"}})
        media = _cache_context({"include_media": True})
        assert len({base, cookies, headers, media}) == 4
        # 同一份凭据 → 同一指纹，否则缓存永不命中
        assert cookies == _cache_context({"cookies": [{"name": "session", "value": "abc"}]})
        # header 顺序不该影响指纹
        assert _cache_context({"extra_headers": {"A": "1", "B": "2"}}) == \
            _cache_context({"extra_headers": {"B": "2", "A": "1"}})

    def test_cache_key_separates_contexts(self):
        from dhole_mcp.cache import _cache_key

        url = "https://example.com/private"
        assert _cache_key(url, "markdown") != _cache_key(url, "markdown", ctx="deadbeef")

    @pytest.mark.asyncio
    async def test_credentialed_body_is_not_served_to_an_anonymous_fetch(self, temp_dir):
        from dhole_mcp.cache import get_cached, set_cached

        url = "https://example.com/private"
        await set_cached(url, "markdown", ["private body"], 200,
                         cache_dir=temp_dir, ctx="A")
        # 匿名请求（ctx=""）看不到带凭据写入的正文
        assert await get_cached(url, "markdown", cache_dir=temp_dir) is None
        hit = await get_cached(url, "markdown", cache_dir=temp_dir, ctx="A")
        assert hit is not None and hit["content"] == ["private body"]

    def test_request_context_decorator_sets_the_fingerprint(self):
        """接线测试：smart_fetch 的请求上下文装饰器负责把指纹放进 ContextVar。

        只看纯函数不够——指纹必须真的在调用期间生效，否则缓存键永远为空。
        """
        import asyncio

        from dhole_mcp import server as server_mod

        captured: dict = {}

        @server_mod._smart_fetch_request_context
        async def fake(self, url="", urls=None, extraction_type="markdown",
                       css_selector=None, main_content_only=True, use_trafilatura=True,
                       cache_ttl=3600, force_fetcher=None, headless=True,
                       real_chrome=False, wait=0, proxy=None, timeout=30000,
                       network_idle=False, solve_cloudflare=True, block_webrtc=True,
                       hide_canvas=True, extra_headers=None, useragent=None, cookies=None,
                       offset=0, max_content_chars=None, pages=None, password=None,
                       focus=None, actions=None, include_media=False, include_links=False,
                       schema=None):
            captured["ctx"] = server_mod._CACHE_CTX.get()

        asyncio.run(fake(None, url="https://x/y", cookies=[{"name": "s", "value": "1"}]))
        credentialed = captured["ctx"]
        assert credentialed != ""

        asyncio.run(fake(None, url="https://x/y"))
        assert captured["ctx"] == "", "指纹必须随调用重置，不能泄漏到下一条请求"

        # 同一份 cookies → 同一个指纹（缓存仍能命中）
        asyncio.run(fake(None, url="https://x/y", cookies=[{"name": "s", "value": "1"}]))
        assert captured["ctx"] == credentialed

    @pytest.mark.asyncio
    async def test_finalize_result_passes_the_context_to_the_cache(self, monkeypatch):
        """写侧接线：_finalize_result 必须把当前指纹交给 set_cached。"""
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_set_cached(url, extraction_type, content, status, css_selector,
                                  ttl, **kwargs):
            seen["ctx"] = kwargs.get("ctx")
            seen["url"] = url

        monkeypatch.setattr(server_mod, "set_cached", fake_set_cached)
        srv = server_mod.MasterFetchServer(cache_ttl=3600)
        result = server_mod._with_agent_hints(server_mod.ResponseModel(
            url="https://example.com/x", status=200, content=["hello"],
        ))
        token = server_mod._CACHE_CTX.set("ctx123")
        try:
            await srv._finalize_result(result, "https://example.com/x", "markdown",
                                       None, 3600)
        finally:
            server_mod._CACHE_CTX.reset(token)
        assert seen.get("url") == "https://example.com/x"
        assert seen.get("ctx") == "ctx123"



# ---------------------------------------------------------------------------
# 隐式域名加权：默认关闭
# ---------------------------------------------------------------------------


class TestSearchFeedbackIsOptIn:
    """``record_search_feedback`` 会按「抓到过就算有用」永久给域名加分
    （+0.05，落盘、上限 500），静默改写跨引擎共识排序。默认必须不生效。"""

    def test_disabled_by_default_and_writes_nothing(self, monkeypatch, temp_dir):
        from dhole_mcp import search

        monkeypatch.delenv("DHOLE_SEARCH_FEEDBACK", raising=False)
        monkeypatch.setattr(search, "_FEEDBACK_FILE", str(temp_dir / "search_feedback.json"))
        monkeypatch.setattr(search, "_feedback_cache", None)
        assert search._feedback_enabled() is False
        assert search._feedback_domains() == frozenset()
        search.record_search_feedback("https://useful.example/page")
        assert not (temp_dir / "search_feedback.json").exists(), "默认不该写反馈文件"
        assert search._domain_boost("https://useful.example/page", "q", False) == 0.0

    def test_opt_in_restores_the_boost(self, monkeypatch, temp_dir):
        from dhole_mcp import search

        monkeypatch.setenv("DHOLE_SEARCH_FEEDBACK", "1")
        monkeypatch.setattr(search, "_FEEDBACK_FILE", str(temp_dir / "search_feedback.json"))
        monkeypatch.setattr(search, "_feedback_cache", None)
        monkeypatch.setattr(search, "_feedback_mtime", 0.0)
        search.record_search_feedback("https://useful.example/page")
        assert search._feedback_domains() == frozenset({"useful.example"})
        assert search._domain_boost("https://useful.example/page", "q", False) == \
            pytest.approx(0.05)


# ---------------------------------------------------------------------------
# 本地调用日志：默认关闭，且不记录参数值
# ---------------------------------------------------------------------------


class TestUsageLogIsOptIn:
    """「agent 到底有没有调用 dhole」是这套工具最大的静默失败面，但没有本地
    记录就无法回答。日志必须默认关闭、且只写工具名与结果，不写参数值。"""

    def test_nothing_written_by_default(self, monkeypatch, temp_dir):
        from dhole_mcp import server

        monkeypatch.delenv("DHOLE_USAGE_LOG", raising=False)
        server._log_tool_call("smart_fetch", True, 12.0)
        assert list(temp_dir.iterdir()) == []

    def test_enabled_records_outcome_without_arguments(self, monkeypatch, temp_dir):
        import json

        from dhole_mcp import server

        target = temp_dir / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(target))
        server._log_tool_call("smart_search", False, 3.5,
                              "boom sk-abcdefghijklmnopqrstuvwxyz")
        line = json.loads(target.read_text(encoding="utf-8").strip())
        assert line["tool"] == "smart_search"
        assert line["ok"] is False
        assert line["ms"] == pytest.approx(3.5)
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in line.get("error", "")


# ---------------------------------------------------------------------------
# 自愈路径：跟随配置的发行版名，且可整体关闭
# ---------------------------------------------------------------------------


class TestRepairFollowsTheConfiguredDistribution:
    """``dhole`` 入口在 ImportError 时会无人值守地 ``pip install
    --force-reinstall``。发行版名曾经硬编码，于是「配置了自己的发行版的 fork」
    仍会从 PyPI 装回公开包（13.x 之前正是这样把上游代码覆盖进本仓库的）。"""

    def test_default_dist_name(self, monkeypatch):
        from dhole_mcp import cli

        monkeypatch.delenv("DHOLE_UPDATE_PACKAGE", raising=False)
        assert cli._dist_name() == "dhole-mcp"

    def test_script_uses_configured_dist_and_index(self, monkeypatch):
        from dhole_mcp import cli

        monkeypatch.setenv("DHOLE_UPDATE_PACKAGE", "my-fork-name")
        monkeypatch.setenv("DHOLE_UPDATE_INDEX_URL", "https://mirror.example/simple")
        script = cli._repair_script_text(cli._dist_name(), cli._index_url())
        assert "my-fork-name" in script
        assert "dhole-mcp" not in script, "自愈脚本不得写死公开发行版名"
        assert "https://mirror.example/simple" in script
        # 生成的脚本必须是可编译的 Python
        compile(script, "repair.py", "exec")

    def test_script_without_index_stays_compilable(self):
        from dhole_mcp import cli

        script = cli._repair_script_text("dhole-mcp", "")
        compile(script, "repair.py", "exec")
        assert "--index-url" not in script

    def test_auto_repair_can_be_disabled(self, monkeypatch):
        from dhole_mcp import cli

        monkeypatch.delenv("DHOLE_NO_AUTO_REPAIR", raising=False)
        assert cli._auto_repair_enabled() is True
        for value in ("1", "true", "YES", "on"):
            monkeypatch.setenv("DHOLE_NO_AUTO_REPAIR", value)
            assert cli._auto_repair_enabled() is False

    def test_updater_repair_script_is_compilable(self, monkeypatch, temp_dir):
        """updater 写出的 repair.py 同样要能编译（含包名/索引替换）。"""
        from dhole_mcp import updater

        monkeypatch.setenv("DHOLE_UPDATE_PACKAGE", "my-fork-name")
        monkeypatch.setenv("DHOLE_UPDATE_INDEX_URL", "https://mirror.example/simple")
        target = temp_dir / "repair.py"
        monkeypatch.setattr(updater, "repair_script_path", lambda: str(target))
        updater._write_repair_script()

        text = target.read_text(encoding="utf-8")
        compile(text, "repair.py", "exec")
        assert "my-fork-name" in text
        assert "dhole-mcp" not in text
        assert "https://mirror.example/simple" in text

    def test_main_does_not_reinstall_when_disabled(self, monkeypatch, capsys):
        """关了自动修复时，ImportError 绝不允许跑 pip。"""
        import builtins

        from dhole_mcp import cli

        monkeypatch.setenv("DHOLE_NO_AUTO_REPAIR", "1")
        monkeypatch.setattr(cli, "_run_repair",
                            lambda: pytest.fail("不应调用自愈（pip 重装）"))

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "dhole_mcp.server":
                raise ModuleNotFoundError("No module named 'dhole_mcp.server'",
                                          name="dhole_mcp.server")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        rc = cli.main()
        out = capsys.readouterr().out
        assert rc == 1
        assert "Auto-repair disabled" in out
        assert "dhole-mcp" in out  # 仍然告诉用户确切的修复命令


# ---------------------------------------------------------------------------
# 面向 agent 的措辞：抓来的正文是数据，不是指令
# ---------------------------------------------------------------------------


def test_instructions_mark_page_text_as_untrusted():
    """抓取结果与服务器自写的 next_action/summary 走同一条信道，容易被模型当成
    同一类「可信指令」。指令里必须显式声明页面正文是不可信数据。"""
    from dhole_mcp.server import DHOLE_INSTRUCTIONS

    low = DHOLE_INSTRUCTIONS.lower()
    assert "untrusted" in low
    assert "never instructions" in low


def test_instructions_do_not_overclaim_officialness():
    """措辞必须与实现一致：is_official 只对 gov/edu/github 为真。"""
    from dhole_mcp.server import DHOLE_INSTRUCTIONS

    assert "gov/edu/github" in DHOLE_INSTRUCTIONS


# ---------------------------------------------------------------------------
# 模型下载源：HF 失败时回退镜像
# ---------------------------------------------------------------------------


class TestRerankerDownloadEndpoints:
    """huggingface.co 在很多国内桌面被 hosts 钉死（本机就是：
    `127.0.0.1 huggingface.co`）。下载源没有回退，模型就永远下不回来，
    神经排序永久静默降级，且没有报错。"""

    def test_default_order_is_hf_then_mirror(self, monkeypatch):
        from dhole_mcp import reranker

        monkeypatch.delenv("DHOLE_HF_ENDPOINT", raising=False)
        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        assert reranker._hf_endpoints() == \
            ["https://huggingface.co", "https://hf-mirror.com"]

    def test_env_override_pins_one_endpoint(self, monkeypatch):
        from dhole_mcp import reranker

        monkeypatch.setenv("DHOLE_HF_ENDPOINT", "https://my-mirror.internal/")
        assert reranker._hf_endpoints() == ["https://my-mirror.internal"]

    def test_urls_keep_the_pinned_revision(self, monkeypatch):
        """换端点不换内容：revision 固定，任何来源的字节一致。"""
        from dhole_mcp import reranker

        urls = reranker._model_urls("model.onnx")
        assert len(urls) >= 2
        assert all(reranker.MODEL_REV in u for u in urls)
        assert all(u.endswith("/onnx/model.onnx") for u in urls)

    def test_download_model_file_falls_back_to_the_next_endpoint(self, monkeypatch):
        """第一个端点失败必须继续试下一个，而不是直接放弃。"""
        from pathlib import Path

        from dhole_mcp import reranker

        tried: list[str] = []

        def _fail(url, dest):
            tried.append(url.split("/")[2])
            return False

        monkeypatch.setattr(reranker, "_download_file", _fail)
        assert reranker._download_model_file("vocab.txt", Path("x")) is False
        assert tried == ["huggingface.co", "hf-mirror.com"]


