"""信封的结构化孪生：`diagnosis` 让 agent 能分支而不是字符串匹配 `error`，
`approx_tokens` 让 agent 能预算上下文。

两条都是"纯增量信号"：prose（`error` / `next_action`）一个字不删，它们是机器可读的
那一份。边界：干净成功不付这份钱（`diagnosis` 缺席 = 成功，`approx_tokens` 为 0 时
缺席），所以压缩层里它们都登记成"停在默认值上就下线"。
"""

from __future__ import annotations

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _approx_tokens, _with_agent_hints
from dhole_mcp.wire import _wire_dump

URL = "https://example.com/page"


def _hinted(**kw) -> ResponseModel:
    base = dict(url=URL, status=200, content=["body"], content_ok=True)
    base.update(kw)
    return _with_agent_hints(ResponseModel(**base))


class TestTheDiagnosis:
    def test_a_clean_success_carries_no_diagnosis(self):
        out = _hinted()
        assert out.diagnosis is None
        assert "diagnosis" not in _wire_dump(out)

    def test_js_shell_names_the_escalation_lever(self):
        out = _hinted(status=200, content=["shell"], error="js_shell_detected: no text")
        assert out.diagnosis["code"] == "js_shell"
        assert out.diagnosis["retryable"] is True
        assert "stealthy" in out.diagnosis["recover_with"]

    def test_a_refusal_is_not_retryable(self):
        out = _hinted(status=200, content=[], error="robots_disallowed: Disallow: /")
        assert out.diagnosis["code"] == "robots_disallowed"
        assert out.diagnosis["retryable"] is False

    def test_a_timeout_is_retryable(self):
        out = _hinted(status=0, content=[],
                      error="timeout: the http tier ran out of budget")
        assert out.diagnosis["code"] == "timeout"
        assert out.diagnosis["retryable"] is True

    def test_an_unlisted_error_still_gets_a_stable_code(self):
        """新错误前缀也能有个稳定的枚举名，不至于静默缺席。"""
        out = _hinted(status=0, content=[], error="weird_new_failure: something happened")
        assert out.diagnosis["code"] == "weird_new_failure"

    def test_a_broken_browser_makes_the_retry_untruthful(self):
        """建议"再抓一次会升级"对一台起不来的浏览器是假话 —— retryable 要读事实。"""
        out = _hinted(status=200, content=["shell"],
                      error="js_shell_detected: no text",
                      escalation_path="browser_binary_missing")
        assert out.diagnosis["code"] == "js_shell"
        assert out.diagnosis["retryable"] is False
        assert "doctor" in out.diagnosis["recover_with"]

    def test_truncation_is_a_reason_to_reason(self):
        out = _hinted(is_truncated=True, next_offset=8000)
        assert out.diagnosis["code"] == "truncated"
        assert out.diagnosis["retryable"] is True
        assert "offset=8000" in out.diagnosis["recover_with"]

    def test_empty_content_without_an_error_is_no_content(self):
        out = _hinted(status=200, content=[])
        assert out.content_ok is False
        assert out.diagnosis["code"] == "no_content"
        assert out.diagnosis["retryable"] is False

    def test_the_diagnosis_rides_the_wire_when_present(self):
        out = _hinted(status=200, content=["shell"], error="js_shell_detected: no text")
        dumped = _wire_dump(out)
        assert dumped["diagnosis"]["code"] == "js_shell"
        assert dumped["diagnosis"]["retryable"] is True


class TestTheTokenEstimate:
    def test_latin_content_is_about_a_quarter(self):
        assert _approx_tokens(["a" * 400]) == 100

    def test_cjk_content_is_not_under_counted(self):
        """中文按 chars/4 会低报约 4 倍 —— 正是要预算上下文的人最怕的方向。"""
        assert _approx_tokens(["中" * 100]) == 100

    def test_mixed_scripts_are_counted_separately(self):
        # 2 个汉字（各 1 token）+ 4 个拉丁字符（1 token）
        assert _approx_tokens(["中文abcd"]) == 3

    def test_empty_content_is_zero_and_off_the_wire(self):
        assert _approx_tokens([]) == 0
        assert _approx_tokens([""]) == 0
        assert "approx_tokens" not in _wire_dump(_hinted(status=200, content=[]))

    def test_it_rides_the_wire_when_there_is_content(self):
        dumped = _wire_dump(_hinted(content=["a" * 40]))
        assert dumped["approx_tokens"] == 10
