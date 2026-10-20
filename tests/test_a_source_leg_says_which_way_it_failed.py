# -*- coding: utf-8 -*-
"""17.0 R02-138 + R02-140 + R02-149：`find_similar` 的源页那条腿要说得出它看见了什么。

`fetch_source_for_similar` 今天把五种事压成 `('','')`（异常 / 4xx·5xx 且没正文 / 零字节 /
命中 blocked 字样 / 有字节但抽不出文本），门里因此只能写「(blocked or offline)」这种两可的话，
`retryable` 只能整颗 True（R02-138）。同一支还**手搓信封**：`next_action` 行首没有本门的申报、
`summary` 整格缺席，而同门九张参数拒绝脸两样都齐（R02-140 · 现场 `r278b`）。第三颗是新量到的
（`r322b` 六发 · 引擎那一侧换成桩 · 零外网）：**源页带回 404 或 JS 壳时门把它当成可用源**，
拿状态页的标题 '404' / 壳页的 '加载中' 去问引擎，而回执一句不说（R02-149）。

判据的形状（不是文本）：
  · 一副失败一个 code，code 由**跨门那张表**给 —— 不许为这条腿另造名字（同一种事在别的门上
    已经叫 `bot_wall` / `no_content` / `extraction_collapsed` / `http_error_<status>`）；
  · `retryable` 跟着原因走（被挡与连不上不是同一种"再发一次会不会变"）；
  · 拒绝信封三格齐全：行首申报 + summary 标签 + error；
  · 带着源往下搜**可以**（404 页也可能真写着东西），但**必须报出口**：状态码与抽出的字数。
桩只打在 `fetcher.HTTPSession` 那一层：helper 的判断、门的装配、表的分类都走真路。
"""
from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from dhole_mcp import fetcher as FT
from dhole_mcp import search as search_mod
from dhole_mcp.server import MasterFetchServer as Server

URL = "https://src.test/page"
CLAUSE = "Nothing was searched - no engine was asked."
# 五种"源页这条腿没拿到可比对的正文"的形状：(站点什么, 期望 code, 期望 retryable)
_PAIRS = [
    # blocked 那颗的 retryable 交回表的答案（True：一面墙下次不一定还在）—— 这一把不替表改判，
    # 只让建议位跟着它写（R02-137 那条法的形状见下面那枚腿）。
    pytest.param("blocked", "bot_wall", True, id="命中 blocked 字样"),
    pytest.param("refused", "connection_refused", False, id="连不上（异常）"),
    pytest.param("empty", "no_content", False, id="200 但零字节"),
    pytest.param("no-text", "extraction_collapsed", False, id="有字节抽不出正文"),
    pytest.param("gone", "http_error_404", False, id="404 且没正文"),
]
KINDS = [p.values[0] for p in _PAIRS]


class _Raises:
    def __init__(self, text):
        self.text = text

    async def get(self, url, **kw):
        raise RuntimeError(self.text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _Answered:
    def __init__(self, status, body):
        self.status = status
        self.status_code = status
        self.body = body
        self.encoding = "utf-8"
        self.headers = {}


class _Answers:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def get(self, url, **kw):
        return _Answered(self.status, self.body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _plant(monkeypatch, kind):
    """把取回那一层换成一种站到的形状（其余全走真路）。"""
    if kind == "blocked":
        body = (b"<html><head><title>Hold on</title></head><body>"
                b"<p>Access Denied: please verify you are a human.</p></body></html>")
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(200, body))
    elif kind == "refused":
        monkeypatch.setattr(
            FT, "HTTPSession",
            lambda **kw: _Raises("[Errno 111] Connection refused"))
    elif kind == "empty":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(200, b""))
    elif kind == "no-text":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(
            200, b"<html><body><img src='/a.png'></body></html>"))
    elif kind == "gone":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(
            404, b"<html><body><img src='/x.png'></body></html>"))
    elif kind == "gone-with-title":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(
            404, b"<html><head><title>404</title></head><body>"
                  b"<p>The page you asked for is gone. It once described how to configure "
                  b"a local fetch tool, with several paragraphs of real prose about proxies, "
                  b"timeouts, and cache lifetimes, long enough to be extracted.</p></body></html>"))
    else:
        raise AssertionError(kind)


def _ask(monkeypatch, kind):
    """跑一次真的门（只有取回层与引擎是桩），读回源页这条腿交出的那一封。"""
    _plant(monkeypatch, kind)
    asked: list = []

    async def spy(query, max_results, **kw):
        asked.append(query)
        return [], []

    real = search_mod.multi_search
    search_mod.multi_search = spy
    try:
        out = asyncio.run(Server().web_search(mode="find_similar", url=URL,
                                              cache_ttl=0))
    finally:
        search_mod.multi_search = real
    return out, asked


class TestEachFailureSaysWhichOne:
    @pytest.mark.parametrize("kind,code,retryable", _PAIRS)
    def test_the_error_leads_with_the_registered_name_for_that_fact(
            self, monkeypatch, kind, code, retryable):
        out, _ = _ask(monkeypatch, kind)
        assert out.error, "这一形状本该被拒，却回了没有 error 的包"
        assert (out.diagnosis or {}).get("code") == code, (kind, out.error, out.diagnosis)
        assert (out.diagnosis or {}).get("retryable") is retryable, (
            kind, out.diagnosis)

    @pytest.mark.parametrize("kind", KINDS)
    def test_the_five_failures_are_not_one_sentence(self, monkeypatch, kind):
        """同一句话装五副形状就是 R02-138：这里钉"五副各一句"。"""
        out, _ = _ask(monkeypatch, kind)
        assert "blocked or offline" not in out.error, out.error
        assert "两可" not in out.error

    def test_no_second_sentence_for_the_same_two_facts(self, monkeypatch):
        """五句要各不相同，也不许两副形状共用一句（0 字节与 404 没正文不是同一件事）。"""
        errs = [_ask(monkeypatch, k)[0].error for k in KINDS]
        assert len(set(errs)) == len(errs), errs


class TestAdviceAndVerdictAgree:
    """R02-137 那条法在这一族上也要成立：写着「Retry」的回执不许报 retryable=false，反之亦然。"""

    @pytest.mark.parametrize("kind", KINDS)
    def test_the_way_out_and_the_handle_answer_the_same_question(self, monkeypatch, kind):
        out, _ = _ask(monkeypatch, kind)
        na = (out.next_action or "").lower()
        says_retry = "retry" in na
        assert says_retry == bool((out.diagnosis or {}).get("retryable")), (
            kind, na[:120], out.diagnosis)


class TestTheEnvelopeIsAssembledNotHandmade:
    @pytest.mark.parametrize("kind", KINDS)
    def test_a_source_leg_refusal_declares_at_its_head(self, monkeypatch, kind):
        out, _ = _ask(monkeypatch, kind)
        assert (out.next_action or "").startswith(CLAUSE), (kind, out.next_action)

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_source_leg_refusal_labels_the_usage_log_line(self, monkeypatch, kind):
        out, _ = _ask(monkeypatch, kind)
        assert (out.summary or "").strip(), (kind, out.summary)

    def test_the_door_does_not_hand_make_a_refusal_envelope(self):
        """拒绝信封由装配位交出：门里不许出现 `return SearchResponseModel(error=<字面句>)`。

        钉的是形状不是文本 —— 把那句搬回门里手搓（哪怕换了措辞、哪怕补了 summary），这里照样红。
        """
        src = inspect.getsource(Server.web_search)
        for i, line in enumerate(src.splitlines(), 1):
            if "SearchResponseModel(" in line:
                tail = "\n".join(src.splitlines()[i - 1:i + 6])
                assert not re.search(r"error=\s*f?[\"']", tail), (
                    f"门里第 {i} 行手搓了一封带字面 error 的拒绝信封：{line.strip()}")


class TestASourceThatAnsweredBadlyIsStillSaidOutLoud:
    """R02-149：404 页带着可读正文时门可以继续用它 —— 但这一件必须写在回执里。"""

    def test_the_404_source_page_is_declared(self, monkeypatch):
        out, asked = _ask(monkeypatch, "gone-with-title")
        assert len(asked) == 1, f"这一形状本该继续搜（行为不改），问到 {asked}"
        blob = " ".join(filter(None, [out.summary, out.next_action, out.fetch_hint]))
        assert "404" in blob, (out.summary, out.next_action, out.fetch_hint)

    def test_declaring_the_404_costs_no_extra_request(self, monkeypatch):
        """报出口不等于替调用方再抓一次：引擎那边只许被问一次，取回层只许被走一次。"""
        calls: list = []
        _plant(monkeypatch, "gone-with-title")
        real_get = _Answers.get

        async def counting_get(self, url, **kw):
            calls.append(url)
            return await real_get(self, url, **kw)

        monkeypatch.setattr(_Answers, "get", counting_get)
        n_engine = []

        async def spy(query, max_results, **kw):
            n_engine.append(query)
            return [], []

        real = search_mod.multi_search
        search_mod.multi_search = spy
        try:
            asyncio.run(Server().web_search(mode="find_similar", url=URL, cache_ttl=0))
        finally:
            search_mod.multi_search = real
        assert len(calls) == 1 and len(n_engine) == 1, (calls, n_engine)
