"""Google CSE：免密池外的 Google 索引入口。

三件事必须钉住：
1. 它只在点名时跑，且鉴权要 **两个值**（API key + cx）；只配 key 不该被读成
   "Google 没有结果"。
2. `num` 钳到 CSE 的硬上限 10；`dateRestrict` 跟着 dhole 的短码走。
3. 索引家族是 `google` —— 它不是 Bing/Brave 的别名，共识分母要单独算。
"""

from __future__ import annotations

import pytest

from dhole_mcp import search_metasearch as m

pytestmark = pytest.mark.smoke


class _Ok:
    status_code = 200
    text = ""

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_google_cse_runs_only_when_named_and_parses_its_items(monkeypatch):
    seen: dict = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return _Ok({"items": [{
            "title": "Attention Is All You Need",
            "link": "https://arxiv.org/abs/1706.03762",
            "snippet": "The dominant sequence transduction models...",
        }]})

    monkeypatch.setenv("DHOLE_GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("DHOLE_GOOGLE_CX", "test-cx")
    monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
    monkeypatch.setattr(m.httpx, "get", fake_get)

    results, status = await m.metasearch(
        "attention", 3, engines=["google_cse"])

    assert status == {"google_cse": "ok"}
    assert results[0]["href"] == "https://arxiv.org/abs/1706.03762"
    assert seen["params"]["q"] == "attention"
    assert seen["params"]["key"] == "test-key"
    assert seen["params"]["cx"] == "test-cx"
    assert seen["params"]["num"] == 7  # max_results + 4, still below the cap


def test_google_cse_caps_num_and_maps_the_date_shortcode(monkeypatch):
    monkeypatch.setenv("DHOLE_GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("DHOLE_GOOGLE_CX", "test-cx")

    endpoint, _headers, payload = m._GoogleCse().build_request("q", 20, "w")

    assert endpoint == m._GoogleCse.endpoint
    assert payload["num"] == 10
    assert payload["dateRestrict"] == "w1"


def test_a_missing_cx_is_an_auth_error_not_an_empty_result(monkeypatch):
    monkeypatch.setenv("DHOLE_GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("DHOLE_GOOGLE_CX", raising=False)

    with pytest.raises(m.GoogleCseAuthError) as exc:
        m._GoogleCse().search_json("q", 3, None)
    assert "DHOLE_GOOGLE_CX" in str(exc.value)


@pytest.mark.asyncio
async def test_only_the_key_configured_is_a_diagnosable_no_key(monkeypatch):
    monkeypatch.delenv("DHOLE_GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("DHOLE_GOOGLE_CX", raising=False)

    with pytest.raises(m.MetaSearchException) as exc:
        await m.metasearch("attention", 3, engines=["google_cse"])
    assert "DHOLE_GOOGLE_API_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_a_missing_cx_surfaces_as_an_auth_error_in_the_engine_report(monkeypatch):
    monkeypatch.setenv("DHOLE_GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("DHOLE_GOOGLE_CX", raising=False)
    monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
    monkeypatch.setattr(m, "_BACKEND_HEALTH", {})
    monkeypatch.setattr(m, "_CONN_FAIL_COUNTS", {})

    results, status = await m.metasearch(
        "attention", 3, engines=["google_cse"])

    assert results == []
    assert status["google_cse"] == "error:GoogleCseAuthError"


def test_google_cse_is_its_own_family_and_knows_dates():
    from dhole_mcp.search_engines import (
        _DATE_AWARE_ENGINES, _INDEX_FAMILY, priority_tier,
    )

    assert _INDEX_FAMILY["google_cse"] == "google"
    assert "google_cse" in _DATE_AWARE_ENGINES
    assert priority_tier("google_cse") == 1
