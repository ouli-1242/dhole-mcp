"""R02-004：显式 timeout 是合同，learned ceiling 只管 dhole 自己的默认。

现场（2026-10-26 · P04 覃川 · S4 · review/round-02）：_http_tier_budget 的
learned 分支对见过的域把每发压到 5s——显式 `timeout=90000` 同样被压
（_adaptive_timeout 对 learned 域无视 default_ms），而 R02-041 的注释自称
"caller's budget is the contract"。调用方付了钱买不到时间；同句还逐发重复
（P08 六连发六遍）。

契约（本文件钉住）：
  * 显式 timeout（wire 上带了 timeout=）→ learned 让位，allowance 照合同；
  * 默认预算 → learned 照旧生效（retry 经济学不破）；
  * learned 生效的那句话要短，且要点名真杠杆（explicit timeout= 能抬）。
"""

from __future__ import annotations

import pytest

from dhole_mcp import context as ctx
from dhole_mcp.server import _http_tier_budget


@pytest.fixture(autouse=True)
def _reset_explicit_flag():
    """ContextVar 是进程内共享的：设过的值会泄漏进同 worker 的后续测试——用完复位。"""
    yield
    ctx._TIMEOUT_EXPLICIT.set(False)


@pytest.fixture
def learned_host(monkeypatch):
    """一台 EMA 1.2s 的快主机：learned = 5s。"""
    import dhole_mcp.server as srv
    monkeypatch.setattr(srv, "_adaptive_timeout", lambda url, default_ms=30000: 5000)
    return srv


def test_a_default_budget_still_yields_to_the_learned_ceiling(learned_host):
    ctx._TIMEOUT_EXPLICIT.set(False)
    per_s, retries, bound = _http_tier_budget("https://fast.test/a", 30_000.0, 30.0)
    assert per_s == 5 and bound == "learned", (per_s, retries, bound)


def test_an_explicit_timeout_is_the_contract(learned_host):
    ctx._TIMEOUT_EXPLICIT.set(True)
    per_s, retries, bound = _http_tier_budget("https://fast.test/a", 30_000.0, 30.0)
    assert per_s == 30 and bound == "", (per_s, retries, bound)


def test_the_learned_sentence_is_short_and_names_the_lever():
    note = ctx._attempt_budget_note("30000", 5, "learned")
    assert note, "learned 生效时该有话"
    assert len(note) <= 160, f"这句话该短：{note}"
    assert "timeout=" in note, f"真杠杆要点名：{note}"


def test_the_left_lever_sentence_survives():
    note = ctx._attempt_budget_note("60000", 12, "left")
    assert "left" in note or "remained" in note, note
