"""`include_headers=true` 交回的是**证据**，不是把响应头原样抄进对话记录。

现场（2026-10-23，外部 agent 实测）：状态码 / 响应头 / 302 链 / Content-Type 这些
"这页为什么是这样"的问题，dhole 一概看不见，只能退回 `curl -D -` —— 而 curl 不在
这条链路上：它没有缓存、没有身份、没有反爬台阶。R3 把这一层补上，本文件钉住四条：

  * `set-cookie` 只交名字（值就是会话令牌，抄进 transcript 等于泄凭据）；
  * 帽（40 颗 / 400 字符）被剪时自己说出来（`_truncated`），不静默；
  * 端点对来自 `original_url`，且缓存回放不重走 HEAD 链；
  * 打开 `include_headers` 就不吃缓存 —— 缓存里的头属于上一次抓取。
"""

from __future__ import annotations

import pytest

from dhole_mcp.models import ResponseModel
from dhole_mcp.server import _attach_header_evidence, _header_snapshot


# ─── 1 · cookie 只留名 ────────────────────────────────────────────────

def test_set_cookie_values_never_reach_the_snapshot():
    snap = _header_snapshot({
        "Set-Cookie": "session=SECRET-TOKEN; Path=/; HttpOnly",
        "Content-Type": "text/html; charset=utf-8",
    })
    assert "SECRET-TOKEN" not in str(snap), snap
    assert snap["set-cookie"] == "cookie name(s) only: session", snap
    assert snap["content-type"] == "text/html; charset=utf-8", snap


def test_several_set_cookie_headers_are_reduced_to_names():
    snap = _header_snapshot({
        "set-cookie": "a=1; Path=/, b=2; Path=/; Expires=Wed, 21 Oct 2026 07:28:00 GMT",
    })
    assert snap["set-cookie"] == "cookie name(s) only: a, b", snap
    assert "=1" not in snap["set-cookie"] and "=2" not in snap["set-cookie"]


# ─── 2 · 帽与自述 ────────────────────────────────────────────────────

def test_a_long_header_value_is_cut_and_says_so():
    snap = _header_snapshot({"x-long": "v" * 900})
    assert len(snap["x-long"]) <= 401, len(snap["x-long"])
    assert snap["x-long"].endswith("…")
    assert "_truncated" in snap


def test_too_many_headers_are_cut_and_say_so():
    snap = _header_snapshot({f"x-{i}": "v" for i in range(60)})
    assert len([k for k in snap if k.startswith("x-")]) == 40
    assert "_truncated" in snap


def test_a_small_header_set_gets_no_truncation_claim():
    snap = _header_snapshot({"date": "now", "server": "x"})
    assert "_truncated" not in snap


# ─── 3 · 端点对与链 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_cached_redirect_reports_the_pair_without_walking_the_chain():
    """缓存回放里那条链属于**写缓存的那一发**；再走一次 HEAD 是问今天的网络。"""
    result = ResponseModel(
        status=200, url="https://new.test/page", content=["ok"],
        original_url="http://old.test/page", cached=True,
    )
    out = await _attach_header_evidence(result, "http://old.test/page")
    redirect = (out.metadata or {}).get("redirect") or {}
    assert redirect == {"from": "http://old.test/page",
                        "to": "https://new.test/page", "hops": 0}, redirect


@pytest.mark.asyncio
async def test_a_page_that_did_not_move_carries_no_redirect_claim():
    result = ResponseModel(status=200, url="https://same.test/page", content=["ok"])
    out = await _attach_header_evidence(result, "https://same.test/page")
    assert "redirect" not in (out.metadata or {}), out.metadata
