"""多命中的元素动作，hint 要指向能修好它的参数，而不是"再试一次"。

现场（2026-10-27，MCP 全量覆盖时发现）：`web_act` 对一个命中 20 个元素的
选择器截图，回执是

    Error: Locator.screenshot: Error: strict mode violation:
    locator("div.image_container") resolved to 20 elements: - call again; if it
    persists, open the URL in a browser to check it renders without a challenge

两次调用都是这一句（第二次换成 `li:nth-child(1) div.image_container` 也还是
20 个：每个 article 各是自己 `<li>` 的头生子，所以 `:first-child` 一个也没排
掉）。"call again" 对一个**选择器形状**问题给的是重试建议，重试必然同一结果；
而真正的门有两个 —— 收窄选择器，或按 snapshot 索引寻址。
"""
from __future__ import annotations

from dhole_mcp.actions import _screenshot_error_message

STRICT = ('Error: Locator.screenshot: Error: strict mode violation: '
          'locator("div.image_container") resolved to 20 elements: \n'
          '    - locator resolved to <div class="image_container">…</div>\n'
          '  - waiting for locator("div.image_container")')


def test_strict_mode_hint_names_the_two_doors():
    msg = _screenshot_error_message(RuntimeError(STRICT))
    low = msg.lower()
    assert "20 elements" in low, msg
    assert "strict mode" in low, msg
    # 两条门都要在：收窄选择器 / 按索引寻址
    assert "nth-child" in low or "first-of-type" in low, msg
    assert "index" in low, msg
    # 重试不是答案
    assert "call again" not in low, msg


def test_the_whole_sentence_survives_the_receipt_cut():
    """动作回执只留 160 字符（`_action_failure` 取首行再截断）。

    这条钉的是「建议必须活到调用方眼前」：驱动前缀 118 字符，按通用形状接建议会
    被截在"该修什么"之前 —— 现场看到的正是 `… resolved to 20 elements: - call
    again`。所以整行要塞进 160。
    """
    msg = _screenshot_error_message(RuntimeError(STRICT))
    assert len(msg) <= 160, f"{len(msg)} 字符，回执里会被截断：{msg}"
    cut = msg[:160].lower()
    assert "nth-child" in cut or "first-of-type" in cut, cut
    assert "index" in cut, cut


def test_a_challenge_timeout_still_gets_the_render_hint():
    """新条目不许抢走原来那条：页面没渲染完时该说的还是 timeout= / wait_selector。"""
    msg = _screenshot_error_message(
        RuntimeError("Timeout 30000ms exceeded while waiting for locator(\"h1\")"))
    assert "raise timeout=" in msg or "wait_selector" in msg, msg
