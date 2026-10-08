"""一次超时不许被分类成 code="error"，也不许一边说"再试一次"一边报不可重试。

实测（2026-10-08，本机回环上一个 6s 才答的端点，`web_read(timeout=1500)`）：

    error      = "error sending request for url (http://127.0.0.1:8919/slow) >
                  operation timed out; the HTTP tier got no answer…"
    diagnosis  = {'code': 'error', 'retryable': False, 'recover_with': ''}
    next_action= "Request timed out. … Retry once with a longer timeout; if it
                  persists, switch sources."

三格互相矛盾：`summary` 知道是网络错，`next_action` 劝人重发，`diagnosis.retryable`
却说不能重发，而 `code` 是从**首词兜底**量出来的 —— `"error sending request…"` 的第一个
词就是 "error"。这一族的前缀表认不出库的原文（表里写的是 `timeout` / `network_error`
这一族我们自己起头的写法），于是兜底规则发明了一个词。

对照实验：同一份预算走 `force_fetcher='stealthy'` 时得到的是 `code='timeout',
retryable=True` —— 那条路径的 error 是我们自己写的句子。所以坏的不是判据，是**谁在
读它**：分类器（`errors.classify_network_error`）一直认得出这串，只是 diagnosis 那条链
没人去问它。

修法把这一问放进同一张表的读法里（`quality.classify_error_text`）：三张表都认不出时，
**先问分类器，再落到首词兜底**。布尔也一并去 `is_retryable` 那张表读，与给人看的那句
hint 同源（D-03 立的规矩：一处判据只许有一个作者）。

首词兜底没有被摘掉 —— 它的本职还在：`nested_no_match: …` 这种自带 code 的串照旧由它
接住（反向守卫在测试里）。
"""
import asyncio
import http.server
import socketserver
import threading
import time

import pytest

from dhole_mcp import quality as Q
from dhole_mcp.errors import is_retryable
from dhole_mcp.quality import classify_error_text, row_diagnosis
from dhole_mcp.server import MasterFetchServer

RAW_TIMEOUT = ("error sending request for url (http://127.0.0.1:8919/slow) > "
               "operation timed out; the HTTP tier got no answer from this target")
RAW_DNS = ("error sending request for url (https://no-such-host-xyzq.invalid/feed) "
           "> tls handshake eof")


@pytest.fixture(scope="module")
def slow_site():
    """一个 6s 才答的回环端点：让超时是**真的发生过**，不是编出来的串。"""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(6)
            body = b"<html><body><p>too late</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class S(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = S(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/slow"
    srv.shutdown()


def test_a_real_timeout_is_classified_as_a_timeout(slow_site, monkeypatch):
    """这一条跑的是**真的超时**：现场的那句原文不许靠手抄重现。"""
    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
    r = asyncio.run(MasterFetchServer().web_read(url=slow_site, cache_ttl=0,
                                                 timeout=1500))
    assert r.status == 0 and "timed out" in (r.error or "").lower(), r.error
    diag = r.diagnosis
    assert diag and diag["code"] != "error", (
        f"首词兜底又把一次超时分类成了 {diag}")
    assert diag["code"] == "timeout", diag
    assert diag["retryable"] is True, diag


def test_the_boolean_and_the_sentence_agree(slow_site, monkeypatch):
    """`retryable` 与 `recover_with` 里那句"Retry once…"必须是同一个判断。"""
    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
    r = asyncio.run(MasterFetchServer().web_read(url=slow_site, cache_ttl=0,
                                                 timeout=1500))
    hint = (r.diagnosis or {}).get("recover_with", "")
    says_retry = "retry" in hint.lower()
    assert says_retry == bool((r.diagnosis or {}).get("retryable")), (
        f"一句劝重发、一格说别重发：{r.diagnosis}")


# ─── 表外形状的读法 ──────────────────────────────────────────────────────────

def test_a_library_dns_failure_does_not_become_the_word_error():
    code, retryable, recover = classify_error_text(RAW_DNS)
    assert code != "error", code
    assert code in ("dns_failure", "tls_error", "connection_reset", "timeout",
                    "unknown"), code


def test_a_refused_connection_is_not_retryable_but_a_reset_is():
    """分类器接手的这一格，布尔必须来自那张表而不是这里再判一次。"""
    refused = "error sending request for url (https://a.example.com/) > os error 10061"
    code, retryable, _ = classify_error_text(refused)
    assert code == "connection_refused", code
    assert retryable is is_retryable("connection_refused") is False
    code2, retryable2, _ = classify_error_text(
        "error sending request for url (https://a.example.com/) > os error 10054")
    assert code2 == "connection_reset"
    assert retryable2 is is_retryable("connection_reset") is True


def test_the_first_word_fallback_is_still_there_for_real_codes():
    """反向守卫：这次补的是"先问分类器"，不是把兜底摘掉。

    `nested_no_match: …`、`no_pages_in_range: …` 这一族自带 code 前缀但不在三张表里，
    照旧由首词兜底接住 —— 摘掉它等于把这些 code 一起弄丢。
    """
    assert classify_error_text("nested_no_match: nothing carried the shape")[0] == \
        "nested_no_match"
    assert row_diagnosis("no_pages_in_range: the requested page range is out of "
                         "bounds")["code"] == "no_pages_in_range"


def test_the_named_codes_still_outrank_the_classifier():
    """表在前，分类器在后：一句 `network_error:` 开头的既有写法不许被改判。"""
    code, retryable, recover = classify_error_text(
        "network_error: dns_failure (TCP preflight)")
    assert code == "network", code
    assert retryable is False
    assert recover == "check network/proxy; the classifier hint in `error` names the fix"


def test_a_plain_http_status_is_not_run_through_the_network_classifier():
    code, _, _ = classify_error_text("http_error_500: server returned error status")
    assert code == "http_500" or code.startswith("http"), code
