"""设计过的参数拒绝不许从 transport 兜底裸逃（2026-10-08 真实使用模拟 · 刀1）。

实测（真宿主，约 70 次调用那一轮）：

  * ``web_crawl(max_content_chars=...)`` → ``{"error": "Unsupported argument(s) ...
    'resume_token', 'schem"}``。支持列表全长 354 字符，被 ``str(e)[:300]`` 剪掉的
    那段正是 ``schema / search / selector / sitemap / timeout / url`` —— 也就是
    "那我该怎么写"。这与 R2刀1 修过的「把出路剪掉」同一类，只是发生在协议边界。
  * ``web_act`` 的 ``fill`` / ``screenshot`` 形状错 → 裸 ``{"error": ...}``，没有
    summary / next_action / content_ok / diagnosis。

transport 兜底的注释写着「走到这里说明这不是 dhole 设计过的拒绝」。这两条都是设计过
的拒绝，却都从那条兜底出去了 —— 于是最需要 next_action 的一条失败反而一个字段都没有。

契约（本文件钉住）：
  * 经**真 SDK 回路**，这类拒绝到达的是标准 invalid_request 信封：有 summary、
    content_ok false、next_action，且 error 的尾巴完整到达（不许被剪）。
  * 分层不许松：``_dispatch`` 自己照旧**抛** ValueError —— CLI 与全仓那十处
    ``raises(ValueError, match="Unsupported argument")`` 的锁读的是异常，信封只在
    协议边界生成。抛的是 ValueError 的子类，旧锁一字不动。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp.server import ArgumentRefusal, MasterFetchServer

SITE = "https://example.com/"


async def _through_the_real_sdk(tool: str, args: dict) -> str:
    """一次真握手：InMemoryTransport 包住的正是 stdio/HTTP 跑的同一个 handler。"""
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return "".join(getattr(b, "text", "") for b in res.content)


def _envelope(text: str) -> dict:
    payload = json.loads(text)
    assert payload.get("error", "").startswith("invalid_request"), (
        f"这不是 invalid_request 信封：{text[:300]}")
    assert payload.get("content_ok") is False, payload
    assert payload.get("next_action"), "设计过的拒绝必须自带出路"
    assert payload.get("summary"), "拒绝不许到达时没有 summary"
    return payload


class TestADesignedRefusalArrivesEnveloped:

    @pytest.mark.asyncio
    async def test_an_unsupported_arg_keeps_its_supported_list_whole(self):
        """被剪掉的尾巴正是解药：支持列表必须整份到达。"""
        text = await _through_the_real_sdk(
            "web_crawl", {"url": SITE, "max_content_chars": 500})
        payload = _envelope(text)
        assert "max_content_chars" in payload["error"]
        for name in ("schema", "search", "selector", "sitemap", "timeout", "url"):
            assert f"'{name}'" in payload["error"], (
                f"支持列表又断了，{name} 没到：{payload['error'][-80:]}")

    @pytest.mark.asyncio
    async def test_a_bad_action_shape_arrives_with_a_way_out(self):
        text = await _through_the_real_sdk(
            "web_act", {"url": SITE,
                        "actions": [{"fill": {"selector": "input", "value": "x"}}]})
        payload = _envelope(text)
        assert "'fill'" in payload["error"], payload["error"]
        assert "text" in payload["error"], "形状错要说清正确的键叫什么"

    @pytest.mark.asyncio
    async def test_a_bare_screenshot_is_refused_in_the_same_shape(self):
        text = await _through_the_real_sdk(
            "web_act", {"url": SITE, "actions": [{"screenshot": True}]})
        payload = _envelope(text)
        assert "screenshot" in payload["error"]
        assert "full_page" in payload["error"], "三种合法形状一种都不许漏"


class TestTheLayeringStaysWhereItWas:
    """信封只在协议边界生成。dispatch 那一层不许跟着改口。"""

    @pytest.mark.asyncio
    async def test_the_refusal_is_still_a_valueerror_subclass(self):
        """旧锁按 ValueError 收，子类不改它们的判据。"""
        assert issubclass(ArgumentRefusal, ValueError)
        with pytest.raises(ValueError, match="Unsupported argument") as boom:
            await MasterFetchServer()._dispatch(
                "web_read", {"url": SITE, "headed": True})
        assert isinstance(boom.value, ArgumentRefusal)
        assert "headed" not in str(boom.value).split("Supported:")[1], (
            "白名单把 headed 放行了")
