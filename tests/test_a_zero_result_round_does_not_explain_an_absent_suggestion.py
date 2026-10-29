"""0 结果的一轮不解释"为什么挖不出后续词"—— 没有行可挖，那句话没有主语。

现场（2026-10-29 · 本机真实 `web_search`，池子全部被限流）：响应 2.8KB，其中约 430
字符是 `fetch_hint` 里这段自述：

    no follow-up phrases this round: this miner pairs two words ... this round's
    rows contain no such pair - 0 whitespace-separated word(s) were found in them ...

这一轮 `results=[]`，`related_queries` 也是空的；唯一的出路（池子没答、去
`sys_state(show=["engine_state"])` 看冷却）已经在 `error`、`next_action` 与池健康
注记里。再向一个没有行的回合解释分词器的内部形状，只会让真正要做的那一步淹掉。

契约：
  * 零结果 → 不出现这句自述；
  * 有结果、但确实挖不出词 → 这句话照旧上线（它是那一格唯一的出路）；
  * 池健康注记不受影响 —— 零结果那一轮该说的话一个字不少。
"""

from __future__ import annotations

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.search_engines import EngineReport


def _stub_empty_round(monkeypatch):
    async def fake_multi(*a, **kw):
        return [], [EngineReport(name="brave", ok=False, blocked=True,
                                 status="blocked", error="HTTP 403")]

    async def fake_none(*a, **k):
        return None

    monkeypatch.setattr(search_mod, "multi_search", fake_multi)
    monkeypatch.setattr(search_mod, "ensure_reranker", fake_none)
    monkeypatch.setattr(search_mod, "get_cached", fake_none)
    monkeypatch.setattr(search_mod, "set_cached", fake_none)
    monkeypatch.setattr(
        search_mod, "_rank",
        lambda query, ranked, mode, **kw: (ranked, [], "merge", ""))


@pytest.mark.asyncio
async def test_zero_results_do_not_get_a_mining_postmortem(monkeypatch):
    _stub_empty_round(monkeypatch)

    resp = await search_mod.web_search(None, "福州 实习 软件")

    assert resp.results == [], resp.results
    assert "no follow-up phrases" not in resp.fetch_hint, resp.fetch_hint
    assert "No results from any engine" in resp.error, (
        f"零结果那一轮该说的话被一起删掉了：{resp.error}")
