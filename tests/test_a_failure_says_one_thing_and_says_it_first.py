"""一条失败只说一件事，而且先说最要紧的那件（2026-10-08 真实使用模拟 · 刀6）

现场（真宿主，`check_login=true` 读 github 的已登录页，被重定向到登录墙）：

    next_action: "page truncated. Use focus='query' … or offset=500 to continue
                  paginating check_login is live-only on the browser tier: re-call
                  with force_fetcher='stealthy' …"

三处坏在一起：① 截断那句抢在了 `auth_wall_detected` 自己的建议之前 —— 而一页登录表单
**翻页翻不出内容**，那条出路是错的；② 两句话之间只有一个空格，读起来是一句坏掉的长句
（`_attach_login_probe_gap` 用 `+ " "` 拼接，前一句没有句末标点）；③ 同一轮里
`nested_no_match` 的 `summary` 是空串、没有 `diagnosis`，而每一条别的拒绝都有。

契约（本文件钉住）：
  * 登录墙 / 反爬墙 / 404 这类"内容本身不是正文"的失败，截断建议不许占位。
  * 两条独立建议拼在一起时必须有句子分隔符，且**更该先做的**排前面。
  * 每一条拒绝都自带 summary 与 diagnosis —— 缺一样就是让调用方自己猜分类。
"""

from __future__ import annotations

import asyncio

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _agent_hints
from dhole_mcp.server import MasterFetchServer


def _wall_result(**kw) -> ResponseModel:
    base = dict(
        url="https://github.com/settings/profile", status=200,
        content=["Sign in to GitHub · GitHub", "[Skip to content](#start-of-content)"],
        content_ok=False, fetcher_used="http", extracted_type="markdown",
        is_truncated=True, next_offset=500, total_extracted_chars=1014,
        page_type="auth_wall",
        error="auth_wall_detected: page is a login/sign-in wall, not content")
    base.update(kw)
    return ResponseModel(**base)


class TestAWallIsNotADocumentToPaginate:

    def test_the_truncation_hint_does_not_win_on_a_login_wall(self):
        na = _agent_hints(_wall_result())[1]
        assert "continue paginating" not in na, na
        assert "login/sign-in wall" in na, na

    def test_a_real_truncation_still_gets_its_pagination_hint(self):
        """反向锁：这一刀不许把对的截断建议一起剪掉。"""
        ok = _wall_result(content_ok=True, error="", page_type="article",
                          content=["# uv", "An extremely fast Python package manager " * 40])
        na = _agent_hints(ok)[1]
        assert "offset=500 to continue paginating" in na, na

    def test_an_error_page_still_prefers_its_own_status(self):
        """404 那一条早就修过同一件事（quality.py:740 的注释）——墙是同一类。"""
        na = _agent_hints(_wall_result(status=404, error="http_error_404: nope"))[1]
        assert "continue paginating" not in na, na


class TestTwoClausesReadAsTwoSentences:

    def test_the_login_probe_clause_is_not_butted_onto_the_previous_advice(self):
        """拼接处要有句子 terminator；前一句已经以标点结尾就不再多加一个。"""
        from dhole_mcp.server import _attach_login_probe_gap

        for tail in ("page is a login/sign-in wall, not content - switch source",
                     "page is a login/sign-in wall, not content - switch source."):
            out = _attach_login_probe_gap(_wall_result(next_action=tail), True)
            joined = out.next_action
            at = joined.find("check_login is live-only")
            assert at > 0, joined
            assert joined[at - 2:at] in (". ", "; ", "! ", "? "), (
                f"两条建议之间只剩一个空格：{joined[:160]!r}")


class TestTheProbeGapNamesTheGapItActuallyHit:
    """`fetcher_used != "stealthy"` 认的是"这一档没跑到"，可它同样匹配"跑到了没跑完"。

    实测（自己进程，无代理打 github 的登录墙）：`fetcher_used='http→stealthy'` +
    `timeout: the 30000ms call budget ran out during the stealthy browser tier`，
    回执却说 "this result never reached the browser tier"，并劝人
    `force_fetcher='stealthy'` —— 正是刚刚烧完预算那一档。
    """

    def test_a_tier_that_ran_out_of_budget_is_not_called_a_tier_that_never_ran(self):
        from dhole_mcp.server import _attach_login_probe_gap

        out = _attach_login_probe_gap(_wall_result(
            fetcher_used="http→stealthy", is_truncated=False,
            content_ok=False, page_type="unknown",
            error="timeout: the 30000ms call budget ran out during the stealthy "
                  "browser tier. No content was extracted."), True)
        assert "never reached the browser tier" not in out.login_check["method"], (
            out.login_check)
        assert "force_fetcher='stealthy'" not in out.next_action, (
            f"劝人重发刚烧完预算那一档：{out.next_action[:160]}")

    def test_a_result_that_genuinely_stayed_on_the_http_tier_still_hears_the_old_line(
            self):
        """反向锁：真没上浏览器档时，那句"用 stealthy 才有探针"仍然要说。"""
        from dhole_mcp.server import _attach_login_probe_gap

        out = _attach_login_probe_gap(
            _wall_result(fetcher_used="http", is_truncated=False,
                         content_ok=True, page_type="article", error=""), True)
        assert "force_fetcher='stealthy'" in out.next_action, out.next_action


class TestEveryRefusalCarriesItsOwnShape:
    """0 命中那条拒绝此前只有 error 与 next_action。"""

    def test_nested_no_match_arrives_with_a_summary_and_a_code(self, monkeypatch):
        from dhole_mcp import server as server_mod

        async def fake(self, *args, **kwargs):
            return ResponseModel(url=kwargs.get("url") or args[0], status=200,
                                 content=["<html><body><p>no cards here</p></body></html>"],
                                 fetcher_used="http", extracted_type="html",
                                 content_type="text/html")

        async def allowed(url, **kw):
            class V:
                allowed = True
                reason = "allowed"
            return V()

        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)
        monkeypatch.setattr(server_mod, "check_robots", allowed)
        out = asyncio.run(MasterFetchServer().web_read(
            url="https://list.test/", cache_ttl=0, nested_selector=".topic-card",
            nested_fields=[{"name": "t", "selector": "a"}]))
        assert out.error.startswith("nested_no_match"), out.error
        assert out.summary, "拒绝不许到达时没有 summary"
        assert out.diagnosis and out.diagnosis["code"] == "nested_no_match", out.diagnosis
        assert out.diagnosis["retryable"] is False, "换参数才有用，重发同一发没用"
