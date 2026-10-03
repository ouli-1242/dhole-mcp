"""回归测试：日志与错误文本里的凭据脱敏 + 若干「开关其实没接线」的历史缺陷。

两类缺陷都来自「看起来无害、实际有副作用」的写法：

1. ``fetcher.py`` 在重试告警里直接写入原始异常文本。primp/httpx 的异常会
   带上完整代理 URL（形如 ``http://user:pass@host``），于是代理凭据落进
   日志。项目里已有 ``security.redact_api_key``，那里此前漏用。
2. 付费引擎把失败响应体原样写进 debug 日志时，key 跟着落盘。注意
   ``redact_api_key`` 的正则只认 ``sk-tinyfish-`` / ``sk|pk|api_key`` 前缀和带
   凭据的代理 URL，**盖不住引擎自己的 key 形状**，所以
   ``TestKeyedEngineErrorBodyRedaction`` 用的是「拿已知 key 做定向替换」，与格式无关。
"""

from __future__ import annotations

import logging

import pytest

from dhole_mcp.fetcher import HTTPSession


class _CredentialLeakingClient:
    """模拟 primp：异常文本里带出完整的带凭据代理 URL。

    两个入口都实现，因为 primp 本来就有两个：``get(url, ...)`` 是
    ``request("GET", url, ...)`` 的便捷写法，而抓取层统一走 ``request``（要发
    POST/PUT/HEAD）。只桩一个的话，测试会在与凭据无关的地方红。
    """

    def request(self, *args, **kwargs):
        raise RuntimeError(
            "connection failed via http://alice:s3cret@proxy.test:8080 "
            "(proxy rejected)"
        )

    def get(self, *args, **kwargs):
        return self.request(*args, **kwargs)


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


_KEYED_KEY = "tvly-dev-0123456789-SECRETDONOTLOG"

class TestKeyedEngineErrorBodyRedaction:
    """Keyed 引擎（Tavily/博查）的失败响应体不得把 key 原样写进日志。"""

    @pytest.mark.asyncio
    async def test_key_in_error_body_does_not_reach_log(self, monkeypatch, caplog):
        import httpx
        import logging

        from dhole_mcp import search_metasearch as m

        class _ServerError:
            status_code = 500
            text = f'{{"error":"unauthorized, key {_KEYED_KEY} rejected"}}'

        monkeypatch.setenv("DHOLE_TAVILY_API_KEY", _KEYED_KEY)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _ServerError())

        with caplog.at_level(logging.DEBUG, logger="dhole_mcp.search_metasearch"):
            results, _status = await m.metasearch("q", 3, engines=["tavily"])

        assert results == []
        assert _KEYED_KEY not in caplog.text, "keyed 引擎的 key 泄漏到了日志"
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
        """接线测试：web_read 的请求上下文装饰器负责把指纹放进 ContextVar。

        只看纯函数不够——指纹必须真的在调用期间生效，否则缓存键永远为空。
        """
        import asyncio

        from dhole_mcp import server as server_mod

        captured: dict = {}

        @server_mod._smart_fetch_request_context
        async def fake(self, url="", urls=None, extraction_type="markdown",
                       css_selector=None,
                       cache_ttl=3600, force_fetcher=None, headless=True,
                       real_chrome=False, wait=0, proxy=None, timeout=30000,
                       network_idle=False, solve_cloudflare=True, block_webrtc=True,
                       hide_canvas=True, extra_headers=None, useragent=None, cookies=None,
                       offset=0, max_content_chars=None, pages=None, password=None,
                       focus=None, actions=None, include_media=False, include_links=False,
                       max_links=None, ignore_robots=False, schema=None):
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

    # ─── PDF 口令维度（14.3 的凭据修复漏了它）────────────────────────

    def test_pdf_password_changes_the_fingerprint(self):
        """口令改变"能不能解出正文"，因此必须进缓存指纹。

        14.3 把 cookies/headers/UA/proxy 四维补进了键，password 却仍是裸的：
        用 password= 解出来的 PDF 正文与同一 URL 的匿名行撞同一个键。
        """
        from dhole_mcp.server import _cache_context

        plain = _cache_context({})
        pw = _cache_context({"password": "s3cret"})
        assert plain == ""
        assert pw and pw != plain
        # 同一口令 → 同一指纹，否则带口令的缓存永不命中
        assert pw == _cache_context({"password": "s3cret"})
        # 明文口令不得出现在指纹里（指纹本身就是缓存键的一部分）
        assert "s3cret" not in pw
        assert _cache_context({"password": ""}) == ""
        assert _cache_context({"password": None}) == ""

    @pytest.mark.asyncio
    async def test_two_passwords_do_not_share_a_row(self, temp_dir):
        from dhole_mcp.server import _cache_context
        from dhole_mcp.cache import get_cached, set_cached

        url = "https://example.com/doc.pdf"
        a = _cache_context({"password": "alpha"})
        b = _cache_context({"password": "bravo"})
        assert a != b
        await set_cached(url, "markdown", ["alpha body"], 200, cache_dir=temp_dir, ctx=a)
        assert await get_cached(url, "markdown", cache_dir=temp_dir, ctx=b) is None
        assert (await get_cached(url, "markdown", cache_dir=temp_dir, ctx=a))["content"] == ["alpha body"]

    @pytest.mark.asyncio
    async def test_decrypted_pdf_body_is_not_served_to_a_passwordless_refetch(self, temp_dir):
        """端到端那一格的缓存侧：匿名请求不能读到别人用口令解出来的正文。"""
        from dhole_mcp.server import _cache_context
        from dhole_mcp.cache import get_cached, set_cached

        url = "https://example.com/financials.pdf"
        ctx = _cache_context({"password": "board-only"})
        await set_cached(url, "markdown", ["DECRYPTED CONFIDENTIAL"], 200,
                         cache_dir=temp_dir, ctx=ctx)
        assert await get_cached(url, "markdown", cache_dir=temp_dir) is None,             "口令解出的正文被匿名请求回放了"

    def test_password_flows_through_the_request_context_decorator(self):
        """接线：password 是真参数，必须真的进指纹（纯函数测试看不到这点）。"""
        import asyncio

        from dhole_mcp import server as server_mod

        captured: dict = {}

        @server_mod._smart_fetch_request_context
        async def fake(self, url="", password=None, pages=None, focus=None,
                       include_media=False, include_links=False, max_links=None,
                       css_selector=None, cookies=None, extra_headers=None,
                       useragent=None, proxy=None):
            captured["ctx"] = server_mod._CACHE_CTX.get()

        asyncio.run(fake(None, url="https://x/a.pdf", password="s3cret"))
        with_pw = captured["ctx"]
        assert with_pw != ""
        asyncio.run(fake(None, url="https://x/a.pdf"))
        assert captured["ctx"] == "", "指纹必须随调用重置"
        asyncio.run(fake(None, url="https://x/a.pdf", password="s3cret"))
        assert captured["ctx"] == with_pw

    @pytest.mark.asyncio
    async def test_legacy_pdf_rows_are_purged_once_and_nothing_else_dies(self, temp_dir):
        """升级只清唯一可能受影响的子集，其余命中照旧。

        整体作废缓存对这个工具有实代价：某些网络上被拦的页面再也抓不回来
        （与 ``paths.rename_legacy_model_dirs`` 为什么存在是同一条理由 —— 有些东西
        不是"重下一次就好"）。
        """
        import aiosqlite

        from dhole_mcp import cache as cache_mod
        from dhole_mcp.cache import get_cached, set_cached

        await set_cached("https://x/a.pdf", "markdown", ["decrypted secret"], 200,
                         cache_dir=temp_dir, css_selector=None, content_type="application/pdf")
        await set_cached("https://x/other.pdf?v=2", "markdown", ["second pdf"], 200,
                         cache_dir=temp_dir)
        await set_cached("https://x/page.html", "markdown", ["normal page"], 200,
                         cache_dir=temp_dir)

        # 假装这是升级前写的库：键版本退回 1
        db = await aiosqlite.connect(temp_dir / "cache.db")
        await db.execute("PRAGMA user_version=1")
        await db.commit()
        await db.close()
        cache_mod._db_initialized.clear()
        await cache_mod._ensure_db(temp_dir)

        assert await get_cached("https://x/a.pdf", "markdown", cache_dir=temp_dir) is None
        assert await get_cached("https://x/other.pdf?v=2", "markdown",
                                cache_dir=temp_dir) is None
        hit = await get_cached("https://x/page.html", "markdown", cache_dir=temp_dir)
        assert hit is not None and hit["content"] == ["normal page"], "误伤了非 PDF 行"

        # 幂等：版本已到位后，新写的 PDF 行不能再被清掉
        await set_cached("https://x/new.pdf", "markdown", ["fresh pdf"], 200, cache_dir=temp_dir)
        cache_mod._db_initialized.clear()
        await cache_mod._ensure_db(temp_dir)
        fresh = await get_cached("https://x/new.pdf", "markdown", cache_dir=temp_dir)
        assert fresh is not None and fresh["content"] == ["fresh pdf"]



# ---------------------------------------------------------------------------
# 隐式域名加权：默认关闭
# ---------------------------------------------------------------------------


class TestSearchFeedbackIsGone:
    """search feedback 域名偏好整块删除（17.0）：旋钮注销、文件不再存在，
    也没有任何代码路径能再写它。"""

    def test_the_machinery_is_gone(self):
        from dhole_mcp import search

        assert not hasattr(search, "_feedback_file")
        assert not hasattr(search, "record_search_feedback")
        assert not hasattr(search, "_feedback_enabled")

    def test_the_knob_changes_nothing(self, monkeypatch, tmp_path):
        from pathlib import Path as _Path
        monkeypatch.setenv("DHOLE_SEARCH_FEEDBACK", "1")
        # 哨兵：任何向 search_feedback.json 的写入都会当场炸掉测试
        sentinel = tmp_path / "search_feedback.json"
        real_write_text = _Path.write_text

        def guard(self, s, *a, **kw):
            if self == sentinel:
                raise AssertionError(f"unexpected write to {self}")
            return real_write_text(self, s, *a, **kw)
        monkeypatch.setattr(_Path, "write_text", guard)
        assert sentinel.exists() is False


class TestTheRepairKnobsMoveNothing:
    """17.0 B2-3 把自愈那次重装整个删了，于是 15.x 的三个旋钮控制的是一件**已经不存在
    的事**：设了它们不该有第二种行为 —— 入口点照旧只打印、照旧返回 1，而那两个
    改包名/改索引的取值更不该出现在任何一句文案里（它们曾经决定"重装谁"）。"""

    @pytest.mark.parametrize("knob,value", [
        ("DHOLE_UPDATE_PACKAGE", "my-fork-name"),
        ("DHOLE_UPDATE_INDEX_URL", "https://mirror.example/simple"),
        ("DHOLE_NO_AUTO_REPAIR", "1"),
    ])
    def test_the_entry_point_behaves_the_same_with_the_stale_knob(
            self, monkeypatch, capsys, knob, value):
        import builtins
        import sys

        from dhole_mcp import cli

        monkeypatch.setenv(knob, value)
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "dhole_mcp.server":
                raise ModuleNotFoundError("No module named 'dhole_mcp.server'",
                                          name="dhole_mcp.server")
            return real_import(name, *args, **kwargs)

        # ops 在别的用例里已经被导入过；不清缓存就不会再执行它那行
        # `from dhole_mcp.server import …`，于是测不到坏安装这条路。
        monkeypatch.delitem(sys.modules, "dhole_mcp.ops", raising=False)
        monkeypatch.setattr(builtins, "__import__", _fake_import)
        rc = cli.main()
        captured = capsys.readouterr()
        text = captured.out + captured.err
        assert rc == 1, "坏安装必须非零退出，否则宿主以为服务起来了"
        assert "pip install" in text, text
        assert not captured.out, "诊断不许写 stdout（那是 MCP 协议通道）"
        for leaked in ("my-fork-name", "mirror.example"):
            assert leaked not in text, f"{leaked} 已经不作数了，却还印在文案里"

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

        for name, model in reranker.MODELS.items():
            urls = reranker._model_urls(model, "model.onnx")
            assert len(urls) >= 2, name
            assert all(model.rev in u for u in urls), name
            assert all(u.endswith("/" + model.relpaths["model.onnx"]) for u in urls), name
            assert all(f"/{model.repo}/" in u for u in urls), name

    def test_default_bge_zh_is_the_int8_bilingual_model(self):
        """450MB fp32 太重：默认必须落在小一号的中英双语 int8 上。"""
        from dhole_mcp import reranker

        default = reranker.MODELS[reranker.DEFAULT_MODEL]
        assert default.name == "bge-zh"
        assert default.relpaths["model.onnx"].endswith("int8.onnx")
        assert default.approx_bytes < 300_000_000
        assert "zh" in default.label or "bilingual" in default.label

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




class TestServerCacheTtlFlagIsLive:
    """`dhole --cache-ttl N` 必须是真旋钮。

    它此前是死的：web_read 的默认值在函数定义时就把模块常量 DEFAULT_TTL 焊进了
    签名，self._cache_ttl 赋值后没有任何读取点。用户唯一能表达"别存这么久"的开关
    不生效，而 README 还列着它。
    """

    class _Stop(Exception):
        """读到 ttl 之后立刻停住 —— 绝不放行到真正的抓取路径（测试必须零网络）。"""

    def _read_ttl(self, monkeypatch, srv, **kw):
        import asyncio

        from dhole_mcp import server as server_mod
        seen: dict = {}

        async def fake_get(url, extraction_type, css_selector, **kwargs):
            seen["read_ttl"] = kwargs.get("ttl")
            return None

        async def stop(*a, **kwargs):
            raise self._Stop()

        monkeypatch.setattr(server_mod, "get_cached", fake_get)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", stop)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_force_fetch", stop)
        with pytest.raises(self._Stop):
            asyncio.run(srv.web_read(url="https://example.com/x", **kw))
        return seen

    def test_instance_default_reaches_the_cache_lookup(self, monkeypatch):
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer(cache_ttl=99)
        seen = self._read_ttl(monkeypatch, srv)
        assert seen.get("read_ttl") == 99, "读取用的 TTL 应来自实例，而非被焊死的 DEFAULT_TTL"

    def test_explicit_argument_still_wins(self, monkeypatch):
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer(cache_ttl=99)
        assert self._read_ttl(monkeypatch, srv, cache_ttl=5).get("read_ttl") == 5

    def test_unset_flag_keeps_the_documented_hour(self, monkeypatch):
        from dhole_mcp.cache import DEFAULT_TTL
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer()
        assert srv._cache_ttl == DEFAULT_TTL
        assert self._read_ttl(monkeypatch, srv).get("read_ttl") == DEFAULT_TTL

    def test_zero_still_skips_the_cache_lookup(self, monkeypatch):
        """cache_ttl=0 = 完全绕开缓存，这条既有语义不能被哨兵改动弄丢。"""
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer()
        assert "read_ttl" not in self._read_ttl(monkeypatch, srv, cache_ttl=0)


class TestPdfPasswordReachesTheExtractorAndTheCache:
    """口令链的接线：选项 → _PDF_PASSWORD → extract_pdf(password=…) → 写缓存用带口令的指纹。

    这条验的是**接线**（用打桩，能指出"口令没传下去"这类错）；pdfplumber 真解密那份
    行为由 tests/test_pdf_real.py 用真加密字节验（pypdf 现场生成样本）。两条合起来
    才闭合：一条证"传到了"，一条证"传到了就真解得开"。
    """

    def test_option_flows_to_the_extractor_and_the_cache_key(self, monkeypatch):
        import asyncio

        from dhole_mcp import pdf_extractor
        from dhole_mcp import server as sm

        seen: dict = {}

        def fake_extract_pdf(body, extraction_type="markdown", pages=None,
                             password=None, include_media=False):
            seen["password"] = password
            # 真实现解开口令后 encrypted 是 False（加密标志只在"打开失败/被拒"时立起，
            # server 用它来决定还该不该走 OCR）—— 桩要跟真行为一致，否则测试在教错事。
            return pdf_extractor.PdfResult(
                content=["DECRYPTED BODY"], encrypted=False, content_ok=True)

        monkeypatch.setattr(pdf_extractor, "extract_pdf", fake_extract_pdf)

        writes: dict = {}

        async def fake_set_cached(url, extraction_type, content, status, css_selector,
                                  ttl, **kwargs):
            writes["ctx"] = kwargs.get("ctx")
            writes["content"] = content

        monkeypatch.setattr(sm, "set_cached", fake_set_cached)

        srv = sm.MasterFetchServer(cache_ttl=3600)
        pw_token = sm._PDF_PASSWORD.set("s3cret")
        ctx_token = sm._CACHE_CTX.set(sm._cache_context({"password": "s3cret"}))
        try:
            res = sm._extract_pdf_response(b"%PDF-1.4 fake", "application/pdf", 13,
                                           "https://x/a.pdf", "markdown", "http", 5.0)
            assert seen["password"] == "s3cret", "口令没传到 extract_pdf"
            assert sm._cache_context({"password": "s3cret"}) != ""
            asyncio.run(srv._finalize_result(res, "https://x/a.pdf", "markdown", None, 3600))
        finally:
            sm._PDF_PASSWORD.reset(pw_token)
            sm._CACHE_CTX.reset(ctx_token)

        assert writes.get("content") == ["DECRYPTED BODY"], "解密正文没进写缓存路径"
        assert writes.get("ctx") == sm._cache_context({"password": "s3cret"}), \
            "带口令解出的正文必须以带口令的指纹写入，否则匿名请求可以复读"
