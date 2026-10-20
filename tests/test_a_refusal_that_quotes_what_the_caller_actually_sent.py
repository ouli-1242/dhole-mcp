# -*- coding: utf-8 -*-
"""R02-018 · 拒绝话术里 `got:` 那一格：它承诺引用调用方发出去的东西，却印着解析的残片。

题面（P02-05 邱洛·S8 跨会话代词）：`web_read(url='上一条里最新的那篇')` 的 error 是
`Only http and https URLs are supported, got: ` —— **冒号后面是空的**。他的原话：
「它不记得上一发我没意见 —— 我也没指望它记得；`got:` 后面空着才是问题。」

读码量到的根因（台账已钉，本文件把它钉成锁）：`security.py:799` 引用的是
`parsed.scheme`，而任何不带 scheme 的输入解析出的 scheme 就是空串。11 发实测把这一族
的两张脸都量出来了（`review/.runtime/p4/f42_measure.py`）：

* **六发印空**：CJK 代词、ASCII 代词、`example.com/page`（有 host 没 scheme）、
  `//example.com/x`、`/tmp/report.pdf`、`http:/example.com` 之外的形状。
* **一发印错东西**：`localhost:8399/x` ⇒ `got: localhost` —— 那不是 scheme，那是
  **被解析器当成 scheme 的 host**。空着让人怀疑参数没传过去，这一发更进一步：
  它把一个 host 说成 scheme，是把人往错方向指。

判据：`got:` 那一格引用**调用方自己发出去的那串**（带上限、走既有 redaction、用 repr
转义控制字符），并且当解析器确实读出一个 scheme 时，说清那是**解析读出来的**而不是
一个关于他输入的判断。修的那条腿不许动安全边界：`file:` / `javascript:` 的拒绝、
反斜杠的 SSRF 拒绝、私网拒绝都逐字原样（§12）。
"""

from __future__ import annotations

import pytest

from dhole_mcp.security import SecurityError, validate_url

_ANCHOR = "Only http and https URLs are supported, got:"


def _face(url: str) -> str:
    with pytest.raises(SecurityError) as got:
        validate_url(url)
    return str(got.value)


# ---- 那一格必须印出调用方发出去的东西 --------------------------------------

@pytest.mark.parametrize("url", [
    "上一条里最新的那篇",
    "the latest one from before",
])
def test_a_pronoun_input_quotes_what_the_caller_actually_sent(url):
    """题面那两发：空格里要有货，而且货是**他那串字**。"""
    text = _face(url)
    assert text.split(_ANCHOR)[1].strip(), text
    assert url in text, text


@pytest.mark.parametrize("url,why", [
    ("example.com/page", "有 host 没 scheme —— 最常见的那一发"),
    ("//example.com/x", "协议相对写法"),
    ("/tmp/report.pdf", "本地路径当 URL 发过来"),
])
def test_a_schemeless_input_says_the_thing_that_is_missing(url, why):
    """不许只印一串引号：没有 scheme 这件事要由这句话自己说出口。"""
    text = _face(url)
    assert "no scheme" in text, (why, text)
    assert url in text, (why, text)


# ---- 解析器读出来的东西要写明是**读出来的** --------------------------------

def test_a_host_port_is_not_relabelled_as_a_scheme():
    """实测的第二张脸：`localhost:8399/x` 从前印 `got: localhost`。

    那既不是空也不是对的 —— 它把 host 说成 scheme。修之后输入原文必须在场，
    而那词要说这是解析读出来的结果，不是对输入的判断。
    """
    text = _face("localhost:8399/x")
    assert "localhost:8399/x" in text, text
    assert "as read from" in text, text
    assert text.split(_ANCHOR)[1].strip() != "localhost", (
        "裸一个 host 在 got: 后面，读起来还是像在说 scheme")


def test_a_real_scheme_is_still_named():
    """既有能力不许退化：`mailto:` 那一发从前就印对了，今天还得印得出名字。"""
    text = _face("mailto:grants@example.org")
    assert "mailto" in text, text
    assert "grants@example.org" in text, text


# ---- 引用别人输入时要付的三张成本 ------------------------------------------

def test_a_long_input_is_quoted_up_to_a_bound_and_says_so():
    """回执不是缓冲区：引用有帽，而帽要**说出口**。"""
    url = "https-not-used/" + "a" * 4_000
    text = _face(url)
    assert len(text) < 400, text[:200]
    assert "not quoted" in text or "more chars" in text, text


def test_the_quote_escapes_what_could_forge_a_line():
    """换行与控制字符不许原样上路（日志伪造 / 回执折行）。"""
    text = _face("上一条\nFAKE: only http and https URLs are supported")
    assert "\n" not in text, repr(text)
    assert "\\n" in text, repr(text)


def test_a_key_inside_the_input_does_not_ride_the_quote():
    """引用调用方的输入不等于把他的凭据抄进回执与日志：走既有 redaction。"""
    secret = "sk-" + "a" * 24
    text = _face(f"example.com/page?api_key={secret}")
    assert secret not in text, text
    assert "[API_KEY_REDACTED]" in text, text


# ---- 不越界：安全边界与别人的句子 ------------------------------------------

@pytest.mark.parametrize("url,keeps", [
    ("file:///C:/Windows/win.ini", "URL scheme 'file' is not allowed"),
    ("javascript:alert(1)", "URL scheme 'javascript' is not allowed"),
    ("http://evil.com\\@127.0.0.1/", "backslash"),
    ("http://127.0.0.1/x", "private"),
    ("", "non-empty string"),
])
def test_the_safety_boundaries_did_not_move(url, keeps):
    """§12：不许为了让句子好看而放宽 robots/私网/凭据/解析那一族的拒绝。"""
    assert keeps.lower() in _face(url).lower(), _face(url)


def test_one_author_for_the_whole_family():
    """装配这一格的作者只许有一位：`got:` 在 src 里出现一次。"""
    import inspect

    import dhole_mcp.security as sec
    src = inspect.getsource(sec)
    assert src.count("URLs are supported, got:") == 1, src.count(
        "URLs are supported, got:")


def test_the_next_action_still_owns_the_prescription():
    """一句处方只有一个作者：error 这一格只说看到了什么，怎么改仍是 next_action 的活。"""
    from dhole_mcp import server as srv
    import inspect
    body = inspect.getsource(srv)
    assert "takes an absolute http(s) URL" in body
    assert "takes an absolute http(s) URL" not in _face("上一条里最新的那篇")


def test_the_manual_says_the_quote_is_the_callers_own_string():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8")
                       for p in guide._ROOT.rglob("*.md"))
    # 两句都得在手册里活着：读手册的 agent 要分得出「没有 scheme」与「解析把某词读成
    # scheme」这两种形状，而那正是这一格从前说不出话的地方。
    assert "no scheme in" in corpus, "手册没写这一格今天引用的是什么"
    assert "' as read from '" in corpus or "as read from" in corpus
    assert "your own argument, quoted back" in corpus, "手册没说引用有帽、有 redaction"
