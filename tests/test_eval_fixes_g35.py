"""G35 —— evals/ 模拟测试暴露的四项缺陷的回归测试。

来源：2026-10-01 对 17.0 的靶站运行（`evals/README.md`）。四项都有独立旁证，
不是"看起来在忙"的发现：

* **D1** 元素截图拿不到图片像素 —— A/B 实验坐实（`disable_resources` 单变量：
  True 时浏览器发出 `GET /qr.png` 0 次，False 时 1 次）
* **D2** 索引寻址的动作标签渲染成 `None`，并泄漏进写操作审计的 `action` 字段
* **D3** `screenshot` / `feed_fetch` 缺 `allow_private`，与 `smart_fetch` 不对称
* **D4** `usage.jsonl` 的 `ok` 对"返回 error 的调用"也记 `True`
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.actions import actions_want_visual, action_label
from dhole_mcp.security import SecurityError, allow_private_scope, validate_url
from dhole_mcp.server import MasterFetchServer, ResponseModel


class _Stop(Exception):
    """Short-circuit a call at the seam under test."""


def _loopback_is_fetchable() -> bool:
    """True when the SSRF guard currently lets a loopback URL through."""
    try:
        validate_url("http://127.0.0.1:8399/")
    except SecurityError:
        return False
    return True


# ─── D1：截图动作必须让资源拦截让路 ──────────────────────────────────────

class TestAScreenshotActionKeepsPageResources:
    """D1。截图是唯一"产品就是渲染结果"的动作，而 stealthy 路径默认关掉
    image/stylesheet/font 提速——在拦截下截元素，得到的是破图占位符，不是
    调用方要看的那张二维码。"""

    @staticmethod
    def _kwargs_seen(monkeypatch, actions):
        seen: dict = {}

        async def fake_force_fetch(self, *args, **kwargs):
            seen.update(kwargs)
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "_force_fetch",
                            fake_force_fetch)
        with pytest.raises(_Stop):
            asyncio.run(MasterFetchServer().smart_fetch(
                url="https://example.com/", actions=actions))
        return seen

    def test_a_screenshot_action_turns_resource_blocking_off(self, monkeypatch):
        monkeypatch.setenv("DHOLE_MULTIMODAL", "1")
        seen = self._kwargs_seen(monkeypatch, [{"screenshot": {"selector": "#qr"}}])
        assert seen["disable_resources"] is False

    def test_actions_without_a_screenshot_keep_the_speed_shortcut(self, monkeypatch):
        seen = self._kwargs_seen(monkeypatch, [{"click": "#more"}])
        assert seen["disable_resources"] is True

    def test_a_snapshot_only_chain_keeps_the_speed_shortcut(self, monkeypatch):
        seen = self._kwargs_seen(monkeypatch, [{"snapshot": True}, {"scroll": 3}])
        assert seen["disable_resources"] is True


class TestActionsWantVisual:
    """D1 的判定函数本身。扫的是**原始列表**，与 `_validate_actions` 同一个键
    测试，所以能在验证之前跑，也不会与解析器分叉。"""

    @pytest.mark.parametrize("actions,expected", [
        (None, False),
        ([], False),
        ("not a list", False),
        ({"screenshot": {"selector": "#qr"}}, False),  # 不是列表
        ([{"click": "#a"}], False),
        ([{"snapshot": True}, {"scroll": 3}], False),
        ([{"screenshot": {"selector": "#qr"}}], True),
        ([{"scroll": 3}, {"screenshot": {"selector": "#qr"}}], True),
        ([{"screenshot": "#qr"}], True),
        (["not a dict", {"screenshot": {"selector": "#qr"}}], True),
    ])
    def test_the_scan(self, actions, expected):
        assert actions_want_visual(actions) is expected


# ─── D2：索引寻址的动作标签 ──────────────────────────────────────────────

class TestIndexAddressedActionLabels:
    """D2。`_act_target` 把 index 寻址归一化成 `{"index": N}`（**不带 selector
    键**），而 `action_label` 的 click/submit 分支仍按 `.get("selector")` 取值，
    于是标签渲染成 `click None` / `submit None (unconfirmed)`。"""

    def test_a_click_by_index_is_named_by_its_index(self):
        assert action_label({"click": {"index": 0}}) == "click [0]"

    def test_a_click_by_index_keeps_the_expect_name(self):
        assert action_label({"click": {"index": 0, "expect": "登录"}}) == "click [0] ~登录"

    def test_a_submit_by_index_names_its_target(self):
        assert action_label({"submit": {"index": 2, "confirm": False}}) == \
            "submit [2] (unconfirmed)"

    def test_a_confirmed_submit_drops_the_gate_note(self):
        assert action_label({"submit": {"index": 2, "confirm": True}}) == "submit [2]"

    def test_a_selector_addressed_submit_is_unchanged(self):
        assert action_label({"submit": {"selector": "#buy", "confirm": False}}) == \
            "submit '#buy' (unconfirmed)"

    @pytest.mark.parametrize("action", [
        {"click": {"index": 0}},
        {"click": {"index": 7, "expect": "结算"}},
        {"fill": {"index": 1, "text": "x"}},
        {"press": {"index": 2, "key": "Enter"}},
        {"submit": {"index": 3}},
        {"submit": {"index": 3, "confirm": True}},
    ])
    def test_no_index_addressed_label_ever_says_none(self, action):
        """一个笼统的守卫：`None` 出现在标签里永远是缺陷，不是某一种动作的
        特例——这四行是给下一个新增动作类型准备的。"""
        label = action_label(action)
        assert "None" not in label, label
        assert "[", f"索引没出现在标签里: {label}"


class TestTheAuditRowCarriesTheSubmitTarget:
    """D2 的后果面。`action` 字段是**写操作审计**里唯一说明"提交了哪个元素"
    的东西，所以标签缺陷泄漏进审计比标签本身难看要严重得多。"""

    def test_a_paused_submit_names_its_target_in_the_audit_row(
            self, monkeypatch, tmp_path: Path):
        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))

        result = ResponseModel(url="https://shop.example.com/checkout",
                               status=200, content=[])
        label = action_label({"submit": {"index": 2, "confirm": False}})
        page_action = type("PA", (), {
            "outcomes": [{"action": label, "ok": False,
                          "pending_approval": True, "error": "pending_approval"}],
        })()

        server_mod._report_action_outcomes(result, page_action)

        rows = [json.loads(ln) for ln in
                log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        writes = [r for r in rows if r.get("event") == "write"]
        assert len(writes) == 1
        assert writes[0]["channel"] == "browser_submit"
        assert writes[0]["pending"] is True
        assert "[2]" in writes[0]["action"], writes[0]["action"]
        assert "None" not in writes[0]["action"], writes[0]["action"]


# ─── D3：私有目标口径统一 ────────────────────────────────────────────────

class TestAllowPrivateScope:
    """D3。`allow_private_scope` 是 smart_fetch 那套机制的可复用形态，给两个
    没走 options 装饰器的工具（screenshot / feed_fetch）用同一套答案，而不是
    再立一套要同步维护的策略。"""

    def test_loopback_is_refused_outside_the_scope(self):
        assert _loopback_is_fetchable() is False

    def test_loopback_is_fetchable_inside_the_scope(self):
        with allow_private_scope(True):
            assert _loopback_is_fetchable() is True

    def test_the_scope_closes_again(self):
        with allow_private_scope(True):
            pass
        assert _loopback_is_fetchable() is False

    def test_a_host_list_does_not_open_all_of_loopback(self):
        with allow_private_scope(["my-service.local"]):
            assert _loopback_is_fetchable() is False

    def test_a_falsy_value_is_a_no_op(self):
        with allow_private_scope(None):
            assert _loopback_is_fetchable() is False


class TestThePrivateTargetOptionReachesBothTools:
    """D3 的接线面。仓库自己有一条不变式——"被转发的选项必须是方法接受的
    参数"（tests/test_crawl_politeness_g27.py）——所以 `allow_private` 是两个
    方法的**真实形参**，作用域开在方法内部、紧挨着它保护的那几行。"""

    def test_the_dispatcher_forwards_the_option_to_feed_fetch(self, monkeypatch):
        seen: dict = {}

        async def fake_feed_fetch(self, **kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(server_mod.MasterFetchServer, "feed_fetch",
                            fake_feed_fetch)
        asyncio.run(MasterFetchServer()._dispatch("feed_fetch", {
            "urls": ["http://127.0.0.1:8399/feed.xml"], "allow_private": True}))

        assert seen["allow_private"] is True

    def test_both_methods_accept_the_option_as_a_real_parameter(self):
        import inspect

        for tool in ("screenshot", "feed_fetch"):
            params = inspect.signature(getattr(MasterFetchServer, tool)).parameters
            assert "allow_private" in params, f"{tool} 不接受 allow_private"

    def test_feed_fetch_opens_the_scope_around_validation_and_fetch(self, monkeypatch):
        """作用域要盖住校验**和**抓取两步：只放开校验那一步，等于放行了 URL
        又在取文档时把它拒掉。"""
        import dhole_mcp.feed as feed_mod

        seen: dict = {}

        async def fake_fetch_feeds(urls, **kwargs):
            seen["urls"] = urls
            seen["loopback_ok"] = _loopback_is_fetchable()
            return []

        monkeypatch.setattr(feed_mod, "fetch_feeds", fake_fetch_feeds)

        out = asyncio.run(MasterFetchServer().feed_fetch(
            urls=["http://127.0.0.1:8399/feed.xml"], allow_private=True))

        assert out == []
        assert seen["urls"] == ["http://127.0.0.1:8399/feed.xml"]
        assert seen["loopback_ok"] is True

    def test_feed_fetch_refuses_a_loopback_url_without_the_option(self):
        with pytest.raises(ValueError) as e:
            asyncio.run(MasterFetchServer().feed_fetch(
                urls=["http://127.0.0.1:8399/feed.xml"]))
        assert "feed_fetch URL 校验失败" in str(e.value)
        assert "internal" in str(e.value)

    def test_screenshot_opens_the_scope_before_validate_url(self, monkeypatch):
        seen: dict = {}
        real_validate = server_mod.validate_url

        def spy(url, *args, **kwargs):
            seen["loopback_ok"] = _loopback_is_fetchable()
            return real_validate(url, *args, **kwargs)

        monkeypatch.setattr(server_mod, "validate_url", spy)
        # 在 validate_url 之后立刻停下，不真起浏览器。
        monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: False)

        with pytest.raises(RuntimeError):
            asyncio.run(MasterFetchServer().screenshot(
                url="http://127.0.0.1:8399/qr", allow_private=True))

        assert seen["loopback_ok"] is True

    def test_screenshot_opens_the_scope_for_the_navigation(self, monkeypatch):
        """导航那一步也要在作用域里：回环页面自己的子资源（那张二维码
        <img>）由浏览器路由处理器判定，读的是同一个上下文变量。只盖住入口
        校验，结果是页面加载了、图被拦了。"""
        seen: dict = {}

        class _Session:
            async def fetch(self, url, **kwargs):
                seen["loopback_ok"] = _loopback_is_fetchable()
                return None

        class _Entry:
            session = _Session()

        async def fake_get_session(self, *args, **kwargs):
            return _Entry()

        monkeypatch.setattr(server_mod.MasterFetchServer, "_get_session",
                            fake_get_session)
        monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)

        with pytest.raises(RuntimeError):  # 假 session 不产出像素，止于"没截到"
            asyncio.run(MasterFetchServer().screenshot(
                url="http://127.0.0.1:8399/qr", session_id="probe",
                allow_private=True))

        assert seen["loopback_ok"] is True

    def test_screenshot_refuses_loopback_without_the_option(self):
        with pytest.raises(SecurityError):
            asyncio.run(MasterFetchServer().screenshot(
                url="http://127.0.0.1:8399/qr"))


# ─── D4：审计日志的 ok 口径 ──────────────────────────────────────────────

class TestTheCallLogRecordsApplicationLevelFailures:
    """D4。`ok` 是"调用跑完了、没抛异常"，不是"工具报告了成功"。一个被 SSRF
    守卫拒掉的 feed_fetch 会走正常 envelope 返回，于是日志里记成
    `{"tool": "feed_fetch", "ok": true, "ms": 22.0}`，拒绝对读者不可见。"""

    def test_a_refusal_envelope_yields_its_error(self):
        payload = {"feeds": [], "error": "feed_fetch URL 校验失败: ..."}
        assert server_mod._result_error(([], payload)).startswith("feed_fetch URL 校验失败")

    def test_a_clean_envelope_yields_nothing(self):
        assert server_mod._result_error(([], {"feeds": [], "error": ""})) == ""
        assert server_mod._result_error(([], {"feeds": []})) == ""

    def test_a_bare_dict_payload_is_read_too(self):
        assert server_mod._result_error({"error": "boom"}) == "boom"

    def test_a_list_payload_reports_the_first_error(self):
        assert server_mod._result_error([{"error": ""}, {"error": "second"}]) == "second"

    def test_a_bare_content_list_yields_nothing(self):
        assert server_mod._result_error([]) == ""

    def test_the_refusal_rides_in_the_existing_error_field(
            self, monkeypatch, tmp_path: Path):
        """端到端：`_log_tool_call` 拿到的第三个字段就是它，`select(.error)`
        能捞出每一次被工具自己判为失败的调用。"""
        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))

        server_mod._log_tool_call(
            "feed_fetch", True, 22.0,
            server_mod._result_error(([], {"feeds": [], "error": "refused"})))

        row = json.loads(log.read_text(encoding="utf-8").strip())
        assert row["ok"] is True          # 调用确实跑完了
        assert row["error"] == "refused"  # 而工具报告了失败
