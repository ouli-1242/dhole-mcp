"""Engine-name registry: a single source of truth for selectable engine names."""

import pytest

from dhole_mcp.search import _validate_engines


class TestBrightdataSelectable:
    """'brightdata' is a real backend, so it must be selectable by name."""

    def test_brightdata_passes_validation(self):
        assert _validate_engines(["brightdata"]) == ["brightdata"]


class TestBrightdataWithoutKey:
    """Asking for the paid backend with no key must blame the key, not the proxy."""

    @pytest.mark.asyncio
    async def test_message_names_missing_key_not_proxy(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.setattr(m, "_BRIGHTDATA_API_KEY", "")
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

        monkeypatch.setattr(m, "_BRIGHTDATA_API_KEY", "fake-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(
            m,
            "_brightdata_serp_search",
            lambda query, max_results=10: [
                SimpleNamespace(title="T", href="https://example.com/a", body="B")
            ],
        )

        results, status = await m.metasearch("test query", 3, engines=["brightdata"])

        assert [r["href"] for r in results] == ["https://example.com/a"]
        assert status.get("brightdata") == "ok"
