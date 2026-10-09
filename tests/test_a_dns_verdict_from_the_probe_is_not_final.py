"""一次 DNS 抖动不该被判成死刑（R5，2026-10-09 真人体验测试 C6 的回归锁）

现场：批量读五个链接，lwn.net 那一发回来的是
`error="network_error: dns_failure (TCP preflight)"` + `diagnosis.retryable=false`
+ next_action 里的 "do NOT retry"。调用方按 `cache_ttl=0` 原样重试，**1643ms 拿到
了页面**。那颗判决出自一次裸 socket 的 `getaddrinfo` —— 一次。它换给调用方的却是
"再试也不会变"，而再试恰好就会变。

这一族错在**判决权**，不在分类：一颗 2 秒的 TCP 预探测同时管着三件事 ——
它给失败起名（`dns_failure`，而 10.1.2.3 是一颗从没被问过的字面 IP）、它决定要不要
跳过 HTTP 与浏览器两档（跳过 = 真实抓取一次都没发生，那句"站点可能挂了"是猜的）、
它还决定这句建议可不可重试。三件事都该由**真拨过的那一档**来说。

四刀（都在这个文件里，逐条反向演示过）：
1. 预探测自己拨两次，间隔 150ms。第二次成功 → 照常去抓，并把"这台机器的 resolver
   要两次才答"写进 `summary`（被吸收掉的故障如果毫无痕迹，就又是一次静默降级）；
   两次都失败才叫 `dns_unresolvable`。
2. 内网目标不再顶着 `dns_failure` 回执 —— 那是假线索，它会把人支去核域名拼写。
   现在它叫 `private_target_blocked`，hint 点名 `allow_private`。不拨内网这一条
   一个字没动（`test_security.TestTcpPreflightIsNotAPortOracle`）。
3. `definitive` 收缩：公网目标上一根线都不再被预探测跳掉；私网保留 refused+timeout
   （那里"2 秒才应答的合法服务"不存在，浏览器拨的是同一个死端口 —— G31 实测 45s）。
4. archive 那次"第二意见"如果没答上，必须留痕（`_note_text` → `summary`）。
   它曾经是三条静默 `return None` 加一句 `logger.debug`。

反向锁同样在这里：私网的 refused 仍然算定论（放宽不能放过头，D-08 那批实测不是
装饰），`connection_refused` 与 `timeout` 的既有出路一字未改。

摘掉修复逐条演示过，每条都真的红：只拨一次 → 本文件 `TestOneBlipIsNotAVerdict`
五条；内网那一支回到 `dns_failure` → 加 `test_security` 的 oracle 两条共 7 红；
抖动那一句不记痕 → 2 红；`definitive` 回到"公网 refused + dns 都跳档" → 4 红（其中
一条是 `test_a_dead_local_port...` 里公网 timeout 该继续升级的正对照）；只把
`dns_unresolvable` 还回跳档名单 → 4 红；archive 的"没答上"回到静默 `return None` →
本文件 `TestTheSecondOpinionLeavesATrail` 红。
"""

from __future__ import annotations

import asyncio
import socket

import pytest

import dhole_mcp.fetcher as fetch_mod
from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.errors import classify_network_error, get_hint, is_retryable
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

PUBLIC = "http://public-flaky.test/article"


class _NoSocket:
    def close(self):
        pass


@pytest.fixture()
def resolver(monkeypatch):
    """把 `create_connection` 换成一台可编程的 resolver：按次序决定第几次拨失败。

    patch 在 `create_connection` 而不是 `getaddrinfo`：conftest 的 `_offline_dns` 已经
    把 `getaddrinfo` 钉在 example.com 的地址上，钉在它下面的东西测不到任何事。
    """
    plan: list[str] = []      # 每一项: "ok" / "dns" / "refused" / "reset" / "timeout"
    attempts: list[str] = []

    def _fake(addr, timeout=None):
        attempts.append(str(addr[0]))
        verdict = plan[len(attempts) - 1] if len(attempts) <= len(plan) else plan[-1]
        if verdict == "ok":
            return _NoSocket()
        if verdict == "dns":
            raise socket.gaierror("Name or service not known")
        if verdict == "refused":
            raise ConnectionRefusedError
        if verdict == "reset":
            raise OSError(10054, "远程主机强迫关闭了一个现有的连接")
        raise socket.timeout

    monkeypatch.setattr(socket, "create_connection", _fake)
    return plan, attempts


class TestOneBlipIsNotAVerdict:

    def test_a_name_that_answers_on_the_second_probe_is_not_a_failure(self, resolver):
        plan, attempts = resolver
        plan[:] = ["dns", "ok"]
        with _arg_notes_scope() as notes:
            ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (True, ""), (ok, category)
        assert len(attempts) == 2, "第二次都没拨，'再试一次'就只是句漂亮话"
        assert any("resolved on the second" in n for n in notes), notes

    def test_the_absorbed_blip_leaves_a_trace(self, resolver):
        """被吸收掉的故障必须 still 说一句：否者调用方永远不知道这台机器在抖。"""
        plan, _ = resolver
        plan[:] = ["dns", "ok"]
        with _arg_notes_scope() as notes:
            fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        note = " ".join(notes)
        assert "public-flaky.test" in note, note
        assert "flaky" in note, note

    def test_two_probes_both_blind_is_called_unresolvable(self, resolver):
        plan, attempts = resolver
        plan[:] = ["dns", "dns"]
        with _arg_notes_scope() as notes:
            ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (False, "dns_unresolvable")
        assert len(attempts) == 2
        assert notes == [], "两次都没答就不是抖动，别在 summary 里留一句抖动的解释"

    def test_the_name_is_not_what_the_caller_is_asked_to_fix(self, resolver):
        """一颗从没问过 DNS 的失败不许顶着一句"去核域名拼写"。

        `0177.0.0.1` 是字面 IP 的回环变体，整条链一次解析都没做过。曾经它是
        `dns_failure`；hint 里那句 "Verify the URL is correct" 让人去核一个不存在
        的域名，而真事是一扇关着的门。
        """
        ok, category = fetch_mod.tcp_preflight("http://0177.0.0.1:8080/", timeout=0.2)
        assert (ok, category) == (False, "private_target_blocked")

    def test_a_second_probe_that_reaches_the_port_is_classified_by_the_port(
            self, resolver):
        """第二次拨通了名字、死在端口上：标签属于第二次，不属于第一次。"""
        plan, _ = resolver
        plan[:] = ["dns", "refused"]
        ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (False, "connection_refused")

    def test_the_gap_between_probes_is_bounded(self, resolver):
        """重试不是无限兜底：只多拨一次。"""
        plan, attempts = resolver
        plan[:] = ["dns", "dns", "ok"]
        ok, category = fetch_mod.tcp_preflight(PUBLIC, timeout=0.2)
        assert (ok, category) == (False, "dns_unresolvable")
        assert len(attempts) == 2, attempts


class TestTheLabelsCarryTheirOwnRetryAnswer:
    """分类、hint、retryable 三件事同处一地（D5 立的法：两边分家会互相否决）。"""

    def test_the_preflight_label_is_read_back_by_the_classifier(self):
        for label in ("dns_unresolvable", "private_target_blocked"):
            category, hint = classify_network_error(
                f"network_error: {label} (TCP preflight)")
            assert category == label, (category, hint)
            assert hint and "unknown" not in hint[:20], hint

    def test_a_name_that_failed_twice_is_not_offered_a_retry(self):
        assert is_retryable("dns_unresolvable") is False
        assert "two probes" in get_hint("dns_unresolvable")
        # 但必须先承认预探测不是最后一个说话的人
        assert "escalation_path" in get_hint("dns_unresolvable")

    def test_a_closed_door_names_the_door_not_the_network(self):
        hint = get_hint("private_target_blocked").lower()
        assert "allow_private" in hint, hint
        assert "no request was sent" in hint, hint
        assert is_retryable("private_target_blocked") is False

    def test_the_old_dns_words_are_untouched(self):
        """正向锁：公网真失败的那句出路一字未改，别让放宽把有用的话冲掉。"""
        assert classify_network_error("net::ERR_NAME_NOT_RESOLVED")[0] == "dns_failure"
        assert is_retryable("dns_failure") is False
        assert is_retryable("connection_refused") is False


class _Calls:

    def __init__(self) -> None:
        self.browser: list[str] = []
        self.archive_urls: list[str] = []

    async def stealthy_one(self, url, *args, **kwargs):
        self.browser.append(url)
        return ResponseModel(url=url, status=200, content=["<p>rendered</p>"],
                             content_type="text/html", fetcher_used="stealthy")

    async def archive(self, url, *args, **kwargs):
        self.archive_urls.append(url)
        return None


@pytest.fixture()
def calls(monkeypatch):
    c = _Calls()
    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", c.stealthy_one)
    monkeypatch.setattr(MasterFetchServer, "_fetch_from_archive", c.archive)

    async def auto_session(self, *args, **kwargs):
        return "stub-session"

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", auto_session)
    return c


def _stub_probe(monkeypatch, category: str) -> None:
    monkeypatch.setattr(fetch_mod, "tcp_preflight",
                        lambda url, timeout=2.0: (False, category))


async def _http_fails(self, url, *args, **kwargs):
    return ResponseModel(
        url=url, status=0, content=[], fetcher_used="http",
        error=("error sending request for url (" + url + ") > operation timed out"),
        duration_ms=10.0)


def _read(url, **kw):
    return asyncio.run(MasterFetchServer().web_read(url=url, cache_ttl=0, **kw))


class TestTheProbeLosesItsPowerToSkipTwoTiers:
    """预探测不再有权跳掉 HTTP + 浏览器两档 —— 除了私网那两个它确实说得准的结论。"""

    def test_a_dns_verdict_from_the_probe_still_lets_the_real_tiers_speak(
            self, monkeypatch, calls):
        _stub_probe(monkeypatch, "dns_unresolvable")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read(PUBLIC)
        assert r.escalation_path and "skipped_http+stealthy" not in r.escalation_path, \
            r.escalation_path
        assert calls.browser == [PUBLIC], (calls.browser, r.escalation_path)

    def test_a_public_refused_connection_no_longer_ends_the_round(
            self, monkeypatch, calls):
        """公网上一次 RST 可能出自中间设备；先应答再失败的那类栈把 refused 当礼貌。"""
        _stub_probe(monkeypatch, "connection_refused")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read(PUBLIC)
        assert calls.browser == [PUBLIC], (calls.browser, r.escalation_path)

    def test_a_private_dead_port_still_stops_here(self, monkeypatch, calls):
        """反向锁（放宽不许放过头）：回环上 refused 与 timeout 都还是定论。

        G31 的量在那儿没有变：浏览器拨的是同一个死端口，实测烧满 45s。
        """
        monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
        _stub_probe(monkeypatch, "connection_refused")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read("http://127.0.0.1:9/x")
        assert "preflight:connection_refused(skipped" in r.escalation_path, \
            r.escalation_path
        assert calls.browser == [], calls.browser

    def test_a_dns_blip_on_a_private_target_also_gets_its_tiers(
            self, monkeypatch, calls):
        """私网保留的是 refused/timeout，不是一次解析失败。

        这条钉的是 `definitive` 里**只有**那两颗：名字解析在回环上通常是 hosts 文件
        或 `localhost` 别名的事，而那是 resolver 的问题，不是那个端口的问题。
        """
        monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
        _stub_probe(monkeypatch, "dns_unresolvable")
        monkeypatch.setattr(MasterFetchServer, "get", _http_fails)
        r = _read("http://127.0.0.1:9/x")
        assert "skipped_http+stealthy" not in (r.escalation_path or ""), r.escalation_path


class TestTheSecondOpinionLeavesATrail:
    """archive 档答不上来时，那一次"问过"必须留在回执里。"""

    @pytest.mark.parametrize("way, wanted", [
        ("no-snapshot", "holds no snapshot"),
        ("api-500", "availability API answered HTTP 500"),
        ("empty-snapshot", "answered HTTP 404 with no readable body"),
        ("raised", "raised before it could answer"),
    ])
    def test_each_way_it_answers_nothing_is_said_differently(
            self, monkeypatch, way, wanted):
        server = MasterFetchServer()

        async def fake_http_get(url, timeout=10):
            from types import SimpleNamespace
            if way == "api-500":
                return SimpleNamespace(status=500, body="")
            if way == "raised":
                raise RuntimeError("socket hang-up")
            # 只有"快照页那一跳"这一支需要 API 真交出一个快照地址，其余三支都在
            # 更早的地方就答不上了。
            return SimpleNamespace(
                status=200,
                body=_wayback_body("has-snapshot" if way == "empty-snapshot" else way))

        async def fake_snapshot_get(self, url, **kwargs):
            return ResponseModel(url=url, status=404, content=["   "],
                                 fetcher_used="http")

        monkeypatch.setattr("dhole_mcp.server._fallback_http_get", fake_http_get)
        monkeypatch.setattr(MasterFetchServer, "get", fake_snapshot_get)
        with _arg_notes_scope() as notes:
            out = asyncio.run(server._fetch_from_archive(PUBLIC, "markdown", None,
                                                         0, 1000))
        assert out is None, way
        blob = " ".join(notes)
        assert wanted in blob, (way, notes)

    def test_a_miss_is_reported_as_a_miss_not_as_an_answer(self, monkeypatch):
        """问过、没有 —— 与"没问过"在 summary 上是两句话，别合并成沉默。"""
        server = MasterFetchServer()

        async def fake_http_get(url, timeout=10):
            from types import SimpleNamespace
            return SimpleNamespace(status=200, body=_wayback_body("no-snapshot"))

        monkeypatch.setattr("dhole_mcp.server._fallback_http_get", fake_http_get)
        with _arg_notes_scope() as notes:
            asyncio.run(server._fetch_from_archive(PUBLIC, "markdown", None, 0, 1000))
        assert len(notes) == 1, notes


def _wayback_body(way: str) -> str:
    import json
    if way == "has-snapshot":
        snap = {"url": "https://web.archive.org/web/2026/snapshot",
                "available": True, "timestamp": "20260101000000"}
    else:
        snap = {"url": "", "available": False}
    return json.dumps({"archived_snapshots": {"closest": snap}})
