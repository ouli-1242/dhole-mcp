"""阶段话不许把 SDK 的 logging 弃用告警刷到宿主 stderr 上。

现场（2026-10-27，MCP 全量覆盖）：每一次带阶段话的调用（web_act / web_search /
web_read 起浏览器那一档）都会在服务端 stderr 打一行

    .../dhole_mcp/context.py:423: MCPDeprecationWarning: The logging capability is
    deprecated as of 2026-07-28 (SEP-2577).
      await session.send_log_message("info", message, logger="dhole.stage")

根因：SDK 的 @deprecated 装饰器**无条件**生效 —— 与协商到的协议版本无关，只要
调用 send_log_message 就告警一次。dhole 还需要这条通道（只实现 logging、不实现
progress 的宿主靠它显示"这一等要几分钟"），所以修法不是换通道，而是在这一个
await 上精确压掉这一类告警。行为不变，噪音消失；别的告警照旧。
"""
from __future__ import annotations

import asyncio
import warnings

import pytest

from mcp.shared.exceptions import MCPDeprecationWarning

from dhole_mcp.context import report_stage, set_mcp_session


class _WarningSession:
    """最小会话替身：progress 成功，logging 一调就抛 SDK 那条弃用告警。"""

    def __init__(self) -> None:
        self.log_calls = 0

    async def report_progress(self, progress, message=None, **kwargs):  # noqa: ARG002
        return None

    async def send_log_message(self, level, data, logger=None, related_request_id=None):  # noqa: ARG002
        self.log_calls += 1
        warnings.warn("The logging capability is deprecated as of 2026-07-28 (SEP-2577).",
                      MCPDeprecationWarning, stacklevel=2)


def test_the_deprecation_warning_does_not_escape_the_stage_message():
    session = _WarningSession()
    set_mcp_session(session)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sent = asyncio.run(report_stage("rendering"))
    finally:
        set_mcp_session(None)

    leaked = [w for w in caught if issubclass(w.category, MCPDeprecationWarning)]
    assert not leaked, [str(w.message) for w in leaked]
    # 压告警不等于掐通道：那一句照发，progress 仍算送达。
    assert session.log_calls == 1
    assert sent is True


def test_other_warnings_are_not_swallowed():
    """只压 MCPDeprecationWarning。别的告警在这条 await 里冒出来照样要能看见。"""

    class _Noisy(_WarningSession):
        async def send_log_message(self, level, data, logger=None, related_request_id=None):  # noqa: ARG002
            warnings.warn("something else is wrong", UserWarning, stacklevel=2)

    session = _Noisy()
    set_mcp_session(session)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            asyncio.run(report_stage("rendering"))
    finally:
        set_mcp_session(None)

    assert any("something else is wrong" in str(w.message) for w in caught), \
        [str(w.message) for w in caught]


@pytest.mark.parametrize("stage", ["fetching", "rendering"])
def test_stage_message_still_returns_true_without_a_session(stage):
    """没有会话时（CLI / 直连 dispatch / 预热）依旧是 False，不是异常。"""
    set_mcp_session(None)
    assert asyncio.run(report_stage(stage)) is False
