"""Engine-name registry + keyed JSON engines (brightdata/tavily/exa/bocha).

策略：keyed 引擎默认不跑，只有 engines= 显式点名才执行 —— 每次调用都花钱。
"""

import httpx
import pytest

from dhole_mcp.search import _validate_engines


class TestBrightdataSelectable:
    """'brightdata' is a real backend, so it must be selectable by name."""

    def test_brightdata_passes_validation(self):
        assert _validate_engines(["brightdata"]) == ["brightdata"]

    def test_all_keyed_names_pass_validation(self):
        from dhole_mcp.search_metasearch import KEYED_ENGINES

        names = sorted(KEYED_ENGINES)
        assert names == ["bocha", "brightdata", "exa", "tavily"]
        assert _validate_engines(names) == names


class TestBrightdataWithoutKey:
    """Asking for the paid backend with no key must blame the key, not the proxy."""

    @pytest.mark.asyncio
    async def test_message_names_missing_key_not_proxy(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.delenv("DHOLE_BRIGHTDATA_API_KEY", raising=False)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)

        with pytest.raises(m.MetaSearchException) as ei:
            await m.metasearch("test query", engines=["brightdata"])

        msg = str(ei.value)
        assert "DHOLE_BRIGHTDATA_API_KEY" in msg
        assert "DHOLE_SEARCH_PROXY" not in msg


class TestBrightdataSelectedWithKey:
    """With a key set, selecting it by name must actually deliver its results."""

    @pytest.mark.asyncio
    async def test_returns_brightdata_results(self, monkeypatch):
        from types import SimpleNamespace

        from dhole_mcp import search_metasearch as m

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "fake-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)

        # 用假的 httpx.post 返回 Bright Data 解析层认识的结构
        import json as _json

        body = _json.dumps({"organic": [
            {"title": "T", "url": "https://example.com/a", "snippet": "B"}
        ]})

        def fake_post(*a, **k):
            return SimpleNamespace(status_code=200, text="", body=body,
                                   json=lambda: {"body": body})

        monkeypatch.setattr(httpx, "post", fake_post)

        results, status = await m.metasearch("test query", 3, engines=["brightdata"])

        assert [r["href"] for r in results] == ["https://example.com/a"]
        assert status.get("brightdata") == "ok"


class TestBrightdataAuthError:
    """A wrong/expired key must not look like 'Google returned nothing'."""

    @pytest.mark.asyncio
    async def test_401_is_distinguishable_from_empty(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        class _Denied:
            status_code = 401
            text = "invalid api key"

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "wrong-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Denied())

        results, status = await m.metasearch("test query", 3, engines=["brightdata"])

        assert results == []
        assert status["brightdata"] == "error:BrightDataAuthError"

    @pytest.mark.asyncio
    async def test_401_does_not_trip_the_circuit_breaker(self, monkeypatch):
        """A bad key is permanent -- cooling it down for 60s fixes nothing."""
        from dhole_mcp import search_metasearch as m

        class _Denied:
            status_code = 403
            text = "zone not authorized"

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "wrong-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Denied())

        await m.metasearch("test query", 3, engines=["brightdata"])

        assert m._is_circuit_open("brightdata") is False


class _OkEmpty:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class TestBrightdataTimeout:
    """The paid call must honour DHOLE_SEARCH_DEADLINE instead of a fixed 20s."""

    @staticmethod
    def _capture(monkeypatch, deadline):
        from dhole_mcp import search_metasearch as m

        seen: dict = {}

        def fake_post(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _OkEmpty()

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "fake-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(m, "_SEARCH_DEADLINE", deadline)
        monkeypatch.setattr(httpx, "post", fake_post)
        return seen

    @pytest.mark.asyncio
    async def test_raised_deadline_reaches_http_timeout(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        seen = self._capture(monkeypatch, 30.0)

        await m.metasearch("test query", 3, engines=["brightdata"])
        assert seen["timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_tight_deadline_keeps_the_paid_call_viable(self, monkeypatch):
        """Floor: a 5s overall deadline must not cut the SERP render short --
        the credit is spent the moment the request goes out."""
        from dhole_mcp import search_metasearch as m

        seen = self._capture(monkeypatch, 5.0)

        await m.metasearch("test query", 3, engines=["brightdata"])
        assert seen["timeout"] == 20.0


class TestNewKeyedEngines:
    """tavily / exa / bocha：可选、可跑、失败可诊断、默认不花钱。"""

    def test_new_names_pass_validation(self):
        assert _validate_engines(["tavily"]) == ["tavily"]
        assert _validate_engines(["exa"]) == ["exa"]
        assert _validate_engines(["bocha"]) == ["bocha"]

    @pytest.mark.asyncio
    async def test_tavily_end_to_end_and_only_tavily_called(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        calls: list = []

        class _TavilyOk:
            status_code = 200

            def json(self):
                return {"results": [
                    {"title": "R1", "url": "https://a.com/1", "content": "正文一"},
                ]}

        def fake_post(url, *a, **k):
            calls.append(url)
            return _TavilyOk()

        monkeypatch.setenv("DHOLE_TAVILY_API_KEY", "tvly-fake")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", fake_post)

        results, status = await m.metasearch("test query", 3, engines=["tavily"])

        assert [r["href"] for r in results] == ["https://a.com/1"]
        assert [r["title"] for r in results] == ["R1"]
        assert status.get("tavily") == "ok"
        assert calls == ["https://api.tavily.com/"], "只应调用 tavily 端点"

    @pytest.mark.asyncio
    async def test_keyed_engines_do_not_run_unselected(self, monkeypatch):
        """策略：不设 engines（走免费默认池）时，任何 keyed 引擎都不许花钱。

        免费后端全部 circuit_open -> 若 keyed 被错误触发，calls 就非空；
        即使因此整体失败，报错也必须指出 keyed 是选填项，而不是让人去配 key。
        """
        from dhole_mcp import search_metasearch as m

        calls: list = []

        class _Empty:
            status_code = 200

            def json(self):
                return {}

        def fake_post(url, *a, **k):
            calls.append(url)
            return _Empty()

        for var in ("DHOLE_BRIGHTDATA_API_KEY", "DHOLE_TAVILY_API_KEY",
                    "DHOLE_EXA_API_KEY", "DHOLE_BOCHA_API_KEY"):
            monkeypatch.setenv(var, "key-set")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", fake_post)
        monkeypatch.setattr(m, "_is_circuit_open", lambda name: True)

        with pytest.raises(m.MetaSearchException) as ei:
            await m.metasearch("test query", 3)

        assert calls == [], "keyed 引擎在未被 engines= 点名时不得发起调用"
        assert "engines=" in str(ei.value), "报错应提示 keyed 引擎是显式选填的"

    @pytest.mark.asyncio
    async def test_exa_402_is_auth_error(self, monkeypatch):
        """实测 Exa 对无 key/欠费回 402 —— 必须可诊断，不进熔断。"""
        from dhole_mcp import search_metasearch as m

        class _PaymentRequired:
            status_code = 402
            text = '{"error":"Payment required to access"}'

        monkeypatch.setenv("DHOLE_EXA_API_KEY", "expired")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _PaymentRequired())

        results, status = await m.metasearch("q", 3, engines=["exa"])

        assert results == []
        assert status["exa"] == "error:ExaAuthError"
        assert m._is_circuit_open("exa") is False

    @pytest.mark.asyncio
    async def test_bocha_maps_timelimit_to_freshness(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        payloads: list = []

        class _BochaOk:
            status_code = 200

            def json(self):
                return {"data": {"webPages": {"value": [
                    {"name": "N", "url": "https://b.cn/1", "snippet": "摘"}
                ]}}}

        def fake_post(url, json=None, *a, **k):
            payloads.append(json)
            return _BochaOk()

        monkeypatch.setenv("DHOLE_BOCHA_API_KEY", "bk")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", fake_post)

        results, status = await m.metasearch("q", 3, engines=["bocha"], timelimit="week")

        assert payloads[-1]["freshness"] == "oneWeek"
        assert payloads[-1]["summary"] is False
        assert [r["href"] for r in results] == ["https://b.cn/1"]

    @pytest.mark.asyncio
    async def test_bocha_without_key_blames_env(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.delenv("DHOLE_BOCHA_API_KEY", raising=False)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)

        with pytest.raises(m.MetaSearchException) as ei:
            await m.metasearch("q", engines=["bocha"])

        assert "DHOLE_BOCHA_API_KEY" in str(ei.value)
