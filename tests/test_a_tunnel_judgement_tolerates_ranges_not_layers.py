"""R3刀2：一个域名的答案不该决定整台机器的复验层；判成隧道也不该让整层停摆。

三条不变式（都往**严**的方向）：

1. 合取 —— 以前的探测只看 example.com 一颗：那颗的答案就决定"这台机器是不是 fake-ip
   隧道"。敌意解析器毒化一颗（或 hosts 里钉一颗、或某条 DIRECT 规则漏一颗）便能叫复验层
   对整台机器停摆。现在两颗独立注册的域名都要答进隧道段才算。
2. 按段豁免 —— verdict=fake-ip 从前是"这一层不看了"：公网名解析到 127.0.0.1 也放行。
   现在复验照跑，被豁免的只有 198.18.0.0/15 与 fc00::/7 这两段（隧道的产物），其余段
   无论这台机器被判成什么都拒。
3. 中途自愈 —— 会话中途才挂上的 TUN 让缓存判词过期（现场：public 缓存 + 公网名进 ULA
   → 全部拒绝 → 只能重启宿主）。现在一次**因隧道段而起的拒绝**之前会重探一次（限流），
   危险段的拒绝不重探（拒对了没什么可自愈）。
"""

import socket

import pytest

from dhole_mcp import security


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """探测缓存、DNS 判定缓存、重探限流的钟都不许跨用例。"""
    monkeypatch.delenv("DHOLE_SSRF_DNS_RECHECK", raising=False)
    security.reset_fake_ip_probe()
    security._DNS_CHECK_CACHE.clear()
    security._last_reprobe_mono = 0.0
    yield
    security.reset_fake_ip_probe()
    security._DNS_CHECK_CACHE.clear()
    security._last_reprobe_mono = 0.0


TUNNEL_V4 = "198.18.0.42"
TUNNEL_V6 = "fd00::19"
PUBLIC_V4 = "93.184.216.34"


def _table_stub(monkeypatch, table, state=None):
    """按名字给答案的 getaddrinfo；``state`` 让同一个测试里改答案（模拟中途挂 TUN）。

    返回被问过的名字列表，"重探发生没有"要能数出来。
    """
    asked = []

    def fake(host, port, *a, **kw):
        asked.append(host)
        pairs = table(host) if callable(table) else table[host]
        if not pairs:
            raise socket.gaierror(11001, "no such host")
        return [(fam, 0, 0, "", (addr, 0)) for fam, addr in pairs]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return asked


# ── 1. 合取：一颗域名不再决定整机 ──────────────────────────────────────────


class TestTheVerdictNeedsTwoIndependentNames:

    def test_a_single_poisoned_answer_flips_the_verdict_off(self, monkeypatch):
        """症状：一个域名的答案决定整机复验层。第二颗答公网，判词就必须是 public。"""
        _table_stub(monkeypatch, {"example.com": [(socket.AF_INET, TUNNEL_V4)],
                                  "example.org": [(socket.AF_INET, PUBLIC_V4)]})
        assert security.probe_fake_ip()[0] == "public"
        assert security._tunnel_exempt_engaged() is False

    def test_two_independent_names_in_range_do_make_a_tunnel(self, monkeypatch):
        _table_stub(monkeypatch, {"example.com": [(socket.AF_INET, TUNNEL_V4)],
                                  "example.org": [(socket.AF_INET6, TUNNEL_V6)]})
        verdict, addr, detail = security.probe_fake_ip()
        assert verdict == "fake-ip" and addr == TUNNEL_V4
        assert "example.com" in detail and "example.org" in detail

    def test_one_name_unanswerable_is_not_either_answer(self, monkeypatch):
        """一颗答不出来（离线 / 被隐藏）不是证据：不确定时一律不放宽。"""
        _table_stub(monkeypatch, {"example.com": [(socket.AF_INET, TUNNEL_V4)],
                                  "example.org": []})
        assert security.probe_fake_ip()[0] == "unprobed"
        assert security._tunnel_exempt_engaged() is False


# ── 2. 按段豁免：复验层不再整层关闭 ────────────────────────────────────────


class TestATunnelToleratesRangesNotTheLayer:

    @pytest.fixture
    def tunnel_host(self, monkeypatch):
        """把这台机器判成隧道，之后目标名各自给出不同地址。"""
        _table_stub(monkeypatch, {"example.com": [(socket.AF_INET, TUNNEL_V4)],
                                  "example.org": [(socket.AF_INET, TUNNEL_V4)]})
        assert security.probe_fake_ip()[0] == "fake-ip"
        return lambda host, pairs: _table_stub(
            monkeypatch, {"example.com": [(socket.AF_INET, TUNNEL_V4)],
                          "example.org": [(socket.AF_INET, TUNNEL_V4)],
                          host: pairs})

    def test_loopback_by_hostname_is_still_refused(self, tunnel_host):
        """旧行为：fake-ip 判定让整层停摆，公网名解析到 127.0.0.1 也不再有人看。"""
        tunnel_host("a.test", [(socket.AF_INET, "127.0.0.1")])
        with pytest.raises(security.SecurityError, match="resolves to internal/private"):
            security.validate_url("https://a.test/x")

    def test_metadata_by_hostname_is_still_refused(self, tunnel_host):
        tunnel_host("a.test", [(socket.AF_INET, "169.254.169.254")])
        with pytest.raises(security.SecurityError, match="resolves to internal/private"):
            security.validate_url("https://a.test/latest/meta-data")

    def test_rfc1918_by_hostname_is_still_refused(self, tunnel_host):
        tunnel_host("a.test", [(socket.AF_INET, "10.0.0.5")])
        with pytest.raises(security.SecurityError):
            security.validate_url("https://a.test/internal")

    def test_the_two_tunnel_ranges_are_tolerated(self, tunnel_host):
        for addr in ("198.18.0.7", "fd00::abcd"):
            security._DNS_CHECK_CACHE.clear()
            tunnel_host("a.test", [(socket.AF_INET6 if ":" in addr else socket.AF_INET,
                                    addr)])
            assert security.validate_url("https://a.test/") == "https://a.test/"

    def test_one_in_range_answer_does_not_hide_another_outside_it(self, tunnel_host):
        """同一个名既给 ULA 又给回环时，豁免只作用于那一条答案，不许在第一段停下。"""
        tunnel_host("a.test", [(socket.AF_INET6, "fd00::1"), (socket.AF_INET, "127.0.0.1")])
        with pytest.raises(security.SecurityError, match="127.0.0.1"):
            security.validate_url("https://a.test/x")

    def test_the_posture_says_what_is_tolerated_and_what_is_not(self, tunnel_host):
        note = security.recheck_posture_note()
        assert "198.18.0.0/15" in note and "fc00::/7" in note
        assert "still refused" in note


# ── 3. 中途自愈：隧道迟到不必重启宿主 ──────────────────────────────────────


class TestATunnelThatArrivedMidSession:

    def test_it_heals_instead_of_refusing_every_public_name(self, monkeypatch):
        """现场：进程缓存着 public，TUN 随后上线，公网名从此解析进 ULA。"""
        state = {"tunnel": False}

        def table(host):
            if host == "a.test":
                return [(socket.AF_INET6, TUNNEL_V6)]
            return ([(socket.AF_INET, TUNNEL_V4)] if state["tunnel"]
                    else [(socket.AF_INET, PUBLIC_V4)])

        asked = _table_stub(monkeypatch, table)
        assert security.probe_fake_ip()[0] == "public"      # 启动时的判词
        state["tunnel"] = True                               # 会话中途挂上 TUN
        probed_before = asked.count("example.com")

        assert security.validate_url("https://a.test/") == "https://a.test/"
        assert asked.count("example.com") > probed_before, "拒绝之前没有重探"
        assert security.probe_fake_ip()[0] == "fake-ip"
        assert security.recheck_posture_note(), "放宽这件事得在回执里看得见"

    def test_the_reprobe_is_rate_limited(self, monkeypatch):
        """限流：一个持续把公网名领进 ULA 的解析器，不该让每次抓取都重探一遍。"""
        def table(host):
            return ([(socket.AF_INET6, TUNNEL_V6)] if host == "a.test" or host == "b.test"
                    else [(socket.AF_INET, PUBLIC_V4)])

        asked = _table_stub(monkeypatch, table)
        assert security.probe_fake_ip()[0] == "public"
        first = len([h for h in asked if h.startswith("example.")])
        with pytest.raises(security.SecurityError):
            security.validate_url("https://a.test/")
        after_first = len([h for h in asked if h.startswith("example.")])
        assert after_first > first, "第一次拒绝前没有重探"

        with pytest.raises(security.SecurityError):
            security.validate_url("https://b.test/")
        after_second = len([h for h in asked if h.startswith("example.")])
        assert after_second == after_first, "限流窗口内又重探了一遍"

    def test_a_dangerous_range_refusal_does_not_reprobe(self, monkeypatch):
        """拒对了没什么可自愈：10/8 不是隧道的产物，重探只会把拒绝变成不确定。"""
        def table(host):
            return [(socket.AF_INET, "10.5.5.5")] if host == "a.test" else [(
                socket.AF_INET, PUBLIC_V4)]

        asked = _table_stub(monkeypatch, table)
        assert security.probe_fake_ip()[0] == "public"
        first = len([h for h in asked if h.startswith("example.")])
        with pytest.raises(security.SecurityError):
            security.validate_url("https://a.test/internal")
        assert len([h for h in asked if h.startswith("example.")]) == first

    def test_a_tunnel_range_refusal_does_not_deny_being_a_tunnel(self, monkeypatch):
        """限流窗口里的第二次隧道段拒绝：那句"代理不会发这种地址"在这里是假话。

        危险段与隧道段的拒绝必须说不同的话 —— 后者的真相是"现判没确认隧道"，不是
        "这种地址不可能是代理的产物"。
        """
        def table(host):
            return ([(socket.AF_INET6, TUNNEL_V6)] if host.startswith(("a.", "b."))
                    else [(socket.AF_INET, PUBLIC_V4)])

        _table_stub(monkeypatch, table)
        assert security.probe_fake_ip()[0] == "public"
        with pytest.raises(security.SecurityError) as first:
            security.validate_url("https://a.test/")
        assert "did not confirm one" in str(first.value), str(first.value)
        with pytest.raises(security.SecurityError) as second:
            security.validate_url("https://b.test/")
        said = str(second.value)
        assert "did not confirm one" in said and "60s" in said, said
        assert "a range a proxy does not hand out" not in said
