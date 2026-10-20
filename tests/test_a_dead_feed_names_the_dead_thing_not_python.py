"""一条读不到的 feed，说的必须是"读不到的那件事"，不是 Python 的内部话。

实测（2026-10-08）`feed_read(urls=["https://no-such-host-xyzq.invalid/feed"])` 交回的
一行是：

    error = "RequestError: error sending request for url
             (…) > tls handshake eof"

三处坏，各自独立：

一、**类名当答案**。`RequestError:` 是实现内部的话。别的通道早就走
    `network_error: <类别> - <出路>` 这一族了，feed 是唯一还在原文上交的。
二、**库的外衣把 DNS 说成 TLS**。primp 把连接阶段的失败统一套上 "tls handshake eof"，
    按串分类就得到"证书问题或网络拦截，别在没改代理之前重试" —— 而真因是那个域名根本
    没解析出地址（`socket.getaddrinfo` 答 gaierror，`fetcher.tcp_preflight` 同判
    dns_failure）。一句假出路比没出路更贵：它把人支去改 TLS 配置。
三、**行上没有 diagnosis**。认 `diagnosis.code` 分支"重试还是换源"的调用方在 feed 上是
    瞎的 —— 只有 `error` 一句人话，等于每个客户端自己再解析一次前缀。

两条不能弯的边界：

* **改判只凭一次名字解析**（`getaddrinfo(host, None)`，端口那一格是空的）。带端口的
  连接就是一次可达性探测，而这条通道的对面可能是内网 —— 说实话不许以多开一扇门为代价。
* **不许把什么都说成 DNS**。名字解析得出来时，真·证书失败必须还是证书失败。
"""
import asyncio
import socket

import pytest

from dhole_mcp import feed as F
from dhole_mcp.quality import classify_error_text, row_diagnosis
from dhole_mcp.server import MasterFetchServer, ResponseModel

URL = "https://no-such-host-xyzq.invalid/feed"
# 逐字取自实测现场（判据认的就是这串，不要美化）
DNS_AS_TLS = ("error sending request for url "
              f"({URL}) > tls handshake eof")
TLS_REAL = ("error sending request for url (https://a.example.com/feed) "
            "> invalid peer certificate: UnknownIssuer")


class _Raises:
    """假 HTTPSession：`get` 抛出现在 `_grab` 的 try 里，让 except 分支走真路。"""

    def __init__(self, text):
        self.text = text

    async def get(self, url, **kw):
        raise RuntimeError(self.text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _run_grab(monkeypatch, text, resolves):
    """跑一次真的 `_grab` 失败路径，只把"名字解析得出吗"换成可控的桩。"""
    monkeypatch.setattr("dhole_mcp.fetcher.HTTPSession", lambda **kw: _Raises(text))
    if resolves:
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    else:
        def _fail(*a, **k):
            raise socket.gaierror(11001, "getaddrinfo failed")
        monkeypatch.setattr(socket, "getaddrinfo", _fail)
    return asyncio.run(F._grab(URL, 5))[3]


# ─── 1 · 类名不再当答案 ───────────────────────────────────────────────────────

def test_the_exception_class_name_stops_leaking(monkeypatch):
    out = _run_grab(monkeypatch, DNS_AS_TLS, resolves=False)
    assert "RequestError" not in out and "RuntimeError" not in out, out
    assert out.startswith("network_error: "), out


# ─── 2 · 外衣改判，但真 TLS 不许被改跑 ───────────────────────────────────────

def test_a_name_that_does_not_resolve_is_called_dns(monkeypatch):
    out = _run_grab(monkeypatch, DNS_AS_TLS, resolves=False)
    assert "dns_failure" in out, out
    assert "tls" not in out.lower(), f"库的外衣还挂在回执上：{out}"
    assert "do NOT retry" in out, "dns 的出路要说清重发不会变"


def test_a_name_that_resolves_keeps_the_tls_verdict(monkeypatch):
    """反向：不许有人拿"失败都算 DNS"图省事。证书坏了就该说证书。"""
    out = _run_grab(monkeypatch, TLS_REAL, resolves=True)
    assert "tls_error" in out, out
    assert "dns" not in out.lower(), out


def test_a_refused_port_stays_refused(monkeypatch):
    out = _run_grab(monkeypatch,
                    "error sending request for url (https://a.example.com/feed) "
                    "> os error 10061", resolves=True)
    assert "connection_refused" in out, out


def test_the_name_check_never_opens_a_port(monkeypatch):
    """`getaddrinfo(host, None)` —— 端口那一格必须是空的。"""
    seen = {}

    def _spy(host, port, *a, **k):
        seen["host"], seen["port"] = host, port
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _spy)
    assert asyncio.run(F._name_resolves("https://someone.example.com/feed")) is True
    assert seen["port"] is None, f"拨端口了：{seen}"
    assert seen["host"] == "someone.example.com"


def test_an_unreadable_host_keeps_the_original_verdict():
    """读不出主机名时不许改判 —— 那时"名字不存在"根本没有依据。"""
    assert asyncio.run(F._name_resolves("not-a-url")) is True


# ─── 3 · 行上有 diagnosis，且与信封读同一张表 ────────────────────────────────

def test_a_not_a_feed_row_gets_a_real_code():
    """`not a feed: …` 落到首词兜底时量出来的 code 是 "not" —— 兜底规则发明的词。"""
    diag = row_diagnosis("not a feed: StartTag: invalid element name, line 1, "
                         "column 2 (the response is an HTML page)")
    assert diag["code"] == "not_a_feed", diag
    assert diag["retryable"] is False
    assert "feed" in diag["recover_with"]


def test_a_successful_row_carries_no_diagnosis():
    """缺席即成功 —— 这一约定不许在第二条通道上变。"""
    assert row_diagnosis("") is None


def test_raw_library_text_becomes_a_real_code_not_error():
    """HTTP 档同款形状的串：code 不许是 "error"，布尔不许与出路相反。"""
    code, retryable, recover = classify_error_text(DNS_AS_TLS)
    assert code != "error", (code, retryable, recover)
    assert code in ("dns_failure", "tls_error", "timeout", "connection_reset",
                    "connection_refused", "unknown"), code


def test_row_and_envelope_agree_on_every_shape():
    """同一个 code 表不许有两个读者：`_diagnose` 与 `row_diagnosis` 必须同答。"""
    from dhole_mcp import quality as Q
    cases = [f"network_error: dns_failure - x", DNS_AS_TLS, "not a feed: StartTag: …",
             "invalid_request: bad since", "HTTP 500", "all_tiers_failed: …",
             "browser_binary_missing: no chrome", "feed too large (9 bytes, cap 1)",
             "timeout: the 1500ms call budget ran out",
             # 状态码那一族换了一位作者之后，两只手仍要同答（R02-134 · F116）：
             # 404 不许多发一次、503 可以 —— 布尔不许在这一格各判一遍。
             "http_error_404: server returned error status",
             "http_error_503: server returned error status",
             "body_too_large: the feed document is 20 bytes, over the 10 cap",
             "invalid_request: since must be a date string (ISO-8601 or an RSS timestamp)"]
    # 只比"有 error 的串"：空 error 时 `_diagnose` 转去读 page_type / content_ok
    # （no_content、truncated 那一族），而行级判据手上只有那一串 —— 两边不该同答。
    for err in cases:
        from_env = Q._diagnose(ResponseModel(url="https://x.test/", status=0,
                                             content=[], error=err))
        from_row = row_diagnosis(err)
        assert (from_env or {}).get("code") == (from_row or {}).get("code"), (
            f"{err[:45]!r} 两条路径各认出一个 code："
            f"{(from_env or {}).get('code')} vs {(from_row or {}).get('code')}")
        assert (from_env or {}).get("retryable") == (from_row or {}).get("retryable"), (
            f"{err[:45]!r} 的 retryable 在两处相反")


# ─── 4 · 端到端：feed_read 的失败行与成功行 ──────────────────────────────────

def test_a_dead_feed_row_arrives_with_the_three_fields(monkeypatch):
    monkeypatch.setattr(F, "_grab", lambda *a, **k: asyncio.sleep(
        0, result=(b"", 0, "", "network_error: dns_failure - DNS resolution failed. "
                               "Verify the URL is correct; do NOT retry.", {})))
    (row,) = asyncio.run(MasterFetchServer().feed_read(urls=[URL]))
    assert row["error"].startswith("network_error:")
    assert row["diagnosis"] == {"code": "network", "retryable": False,
                                "recover_with": row["diagnosis"]["recover_with"]}, \
        row["diagnosis"]
    assert row["items"] == []


def test_a_live_feed_row_still_carries_items_and_no_diagnosis(monkeypatch):
    """反向：结构化判据不许把成功行弄坏，也不许给成功行挂一格空的 diagnosis。"""
    body = (b"<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
            b"<item><title>one</title><link>https://a.example.com/1</link>"
            b"<guid>g1</guid></item></channel></rss>")
    monkeypatch.setattr(F, "_grab",
                        lambda *a, **k: asyncio.sleep(0, result=(body, 200, "utf-8", "", {})))
    (row,) = asyncio.run(MasterFetchServer().feed_read(
        urls=["https://a.example.com/feed"]))
    assert row["error"] == "" and row["diagnosis"] is None
    assert len(row["items"]) == 1, "成功行被这次改动弄坏了"


def test_the_first_word_fallback_only_serves_real_codes():
    """首词兜底的本职：`nested_no_match: …` 这种自带 code 的串。

    它同时也会把一句英文的第一个词当 code（"not a feed" → "not"），那正是这一族要
    进表的原因 —— 兜底是给**已经按 code 写的**错误用的，不是给散文用的。
    """
    assert row_diagnosis("nested_no_match: nothing carried the shape")["code"] ==         "nested_no_match"
    assert row_diagnosis("") is None


def test_every_feed_failure_shape_names_a_code(monkeypatch):
    """三个产地（since 拒绝 / 抓取失败 / 解析失败）都要有判据，不是只补我量到那一处。"""
    ok_body = (b"<?xml version='1.0'?><rss><channel><title>t</title></channel></rss>")
    cases = {
        "since 被拒": dict(urls=[URL], since="not-a-date"),
        "抓取失败": dict(urls=[URL]),
        "不是 feed": dict(urls=[URL]),
    }
    def _grab_factory(which):
        async def _g(url, timeout, headers=None):
            if which == "抓取失败":
                return b"", 0, "", ("network_error: connection_refused - target refused"), {}
            return ok_body, 200, "utf-8", "", {}
        return _g

    for which, kw in cases.items():
        monkeypatch.setattr(F, "_grab", _grab_factory(which))
        (row,) = asyncio.run(MasterFetchServer().feed_read(**kw))
        if row["error"]:
            assert row["diagnosis"] and row["diagnosis"]["code"] not in ("", "not",
                                                                        "error"), (
                f"{which}：行上的 code 是兜底发明的词 —— {row['diagnosis']}")


# ─── 5 · 站点**答过**的状态码只许有一位句子作者（R02-134 · F116）─────────────
#
# 现场（`review/.runtime/p1/r282_same_404_across_doors.json` · 本机靶站 · 零外网）：同一
# 件事实「origin 回了 404」有两位句子作者 —— 在册的那位写 `http_error_404: server returned
# error status`（`quality._detect_content_issue`，`web_read` 的信封与 `web_crawl` 的行都从
# 它取词），第二位是 `feed._grab` 的裸 `HTTP 404`。裸的那一句既不在三张表里也不带自记号，
# 于是 `row_diagnosis` 认不出、那一行**整格没有把手**，而这扇门的 `next_action` 正指着
# 「逐行的 diagnosis」。r284 又量出同族两支：`feed too large (…)` 与 `since …` 两句由
# feed 自家写、code 却由首词兜底发明（`feed` / `since`）—— 那是同一件事的第二种坏法。
#
# 边界（不许顺手做的两件事）：
#   · 不许把裸的 `HTTP 404` 塞进 `_DIAGNOSIS_PREFIXES`（那是给第二位作者补户口，从此一件
#     事实两个名字）；要改的是**产地**，让它问那位在册作者；
#   · `retryable` 不许在这一格另判一遍：那颗数说什么，与同一封回执 `next_action` 说的
#     「Retry once after a pause」（5xx）/「wait before retrying」（429）必须是同一句话，
#     否则两只手又互相否决（D5）。404 那一支照旧 false —— 站方说「没有这个页面」，重发
#     同一发不会变。

class _Answers:
    """假 HTTPSession：`get` 交回一个**答过的**状态码（不是异常，也没有正文）。"""

    def __init__(self, status, body=b""):
        self.status, self.body = status, body

    async def get(self, url, **kw):
        return _Answered(self.status, self.body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _Answered:
    status_code = 0
    body = b""
    headers = None
    encoding = "utf-8"

    def __init__(self, status, body):
        self.status_code, self.body, self.encoding = status, body, "utf-8"
        self.headers = {}


def _grab_answer(monkeypatch, status, body=b"", cap=None):
    """跑一次真的 `_grab`（只有 session 是桩），把上线那一格的原句读回来。"""
    monkeypatch.setattr("dhole_mcp.fetcher.HTTPSession", lambda **kw: _Answers(status, body))
    if cap is not None:
        monkeypatch.setattr(F, "_MAX_FEED_BYTES", cap)
    return asyncio.run(F._grab(URL, 5))


def test_an_answered_status_is_named_by_the_registered_author(monkeypatch):
    """feed 的行不许自己造一句「HTTP 404」：它要问那位在册作者，取到同一串。"""
    from dhole_mcp.quality import status_error_sentence

    err = _grab_answer(monkeypatch, 404)[3]
    assert err == status_error_sentence(404), err
    assert err != "HTTP 404", "第二位作者还在写裸句子"
    diag = row_diagnosis(err)
    assert diag and diag["code"] == "http_error_404", diag
    assert diag["retryable"] is False, diag


def test_the_same_answered_status_is_named_once_in_every_channel(monkeypatch):
    """同一颗 404 在 feed 行 / web_read 信封 / 爬行的行上是同一个名字（三处都读作者）。"""
    from dhole_mcp import quality as Q
    from dhole_mcp.models import ResponseModel

    feed_err = _grab_answer(monkeypatch, 404)[3]
    web_read_err = Q._detect_content_issue(
        ResponseModel(url="https://origin.test/page", status=404, content=[], error=""))
    assert feed_err == web_read_err, (feed_err[:70], web_read_err[:70])
    assert row_diagnosis(feed_err)["code"] == row_diagnosis(web_read_err)["code"]


@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
def test_a_status_that_invites_a_retry_says_so_in_both_hands(monkeypatch, status):
    """布尔不许比建议保守：同一封回执里 `next_action` 让人等一会儿再发，`retryable` 就是真。"""
    from dhole_mcp import quality as Q
    from dhole_mcp.models import ResponseModel

    err = _grab_answer(monkeypatch, status)[3]
    diag = row_diagnosis(err)
    assert diag and diag["code"] == f"http_error_{status}", diag
    assert diag["retryable"] is True, f"{status} 说「重发不会变」：{diag}"
    assert diag["recover_with"], f"说要重发却不指杠杆：{diag}"
    advice = Q._agent_hints(ResponseModel(url="https://origin.test/page", status=status,
                                         content=[], error=err))[1]
    assert "retry" in advice.lower(), f"两只手对不上：{advice[:120]} vs {diag}"


@pytest.mark.parametrize("status", [404, 410, 451])
def test_a_permanent_answer_is_not_invited_into_a_second_try(monkeypatch, status):
    """反向锁：不许有人为了让 5xx 说真话而把这族一并翻成「可重试」。"""
    err = _grab_answer(monkeypatch, status)[3]
    diag = row_diagnosis(err)
    assert diag and diag["code"] == f"http_error_{status}", diag
    assert diag["retryable"] is False, f"{status} 的把手在劝人重发同一发：{diag}"


def test_a_feed_bigger_than_its_own_cap_is_named_by_a_token(monkeypatch):
    """`feed too large (…)` 的 code 从前是兜底按第一个词造的（`feed`）—— 名字要住在作者侧。"""
    err = _grab_answer(monkeypatch, 200, body=b"x" * 40, cap=10)[3]
    assert err.startswith("body_too_large:"), err
    assert not err.startswith("feed too large"), err
    diag = row_diagnosis(err)
    assert diag["code"] == "body_too_large", diag
    assert diag["retryable"] is False, diag


def test_a_rejected_since_is_named_as_the_parameter_refusal_it_is():
    """`since` 读不出日期是**参数**拒绝：它的名字与该门别面的参数拒绝同一颗，不是 `since`。"""
    row = asyncio.run(F.fetch_feed(URL, since="not-a-date"))
    assert row.error.startswith("invalid_request: "), row.error
    assert row.diagnosis["code"] == "invalid_request", row.diagnosis
    again = asyncio.run(F.fetch_feed(URL, since=12345))
    assert again.diagnosis["code"] == "invalid_request", again.diagnosis

