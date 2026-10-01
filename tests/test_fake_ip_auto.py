"""fake-IP 环境自测自适配（W12）。

这个默认值曾经是新手的第一道墙：Clash / sing-box 的 TUN + fake-ip 解析器给**每一
个公网域名**都返回保留地址，于是 fail-closed 的 DNS 复检把整台机器判成内网，
smart_fetch / smart_search 全部失败 —— 而唯一的出路是**事先知道**要设
`DHOLE_SSRF_DNS_RECHECK=0`。README 里那句"零配置即可用"在这台机器上是被实测证伪
的（见 CHANGELOG W3）。

现在改由环境自己回答：探测到 fake-ip 就跳过这一层，跳过这件事在回包里可见，并且
留一条硬回的路。下面每一条都在守这个边界的一侧 —— 放宽的只有 DNS 复检这一层。
"""

import socket

import pytest

from dhole_mcp import security


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """探测缓存与 DNS 判定缓存都不许跨用例；env 里也不许留着这个变量。"""
    monkeypatch.delenv("DHOLE_SSRF_DNS_RECHECK", raising=False)
    security.reset_fake_ip_probe()
    security._DNS_CHECK_CACHE.clear()
    yield
    security.reset_fake_ip_probe()
    security._DNS_CHECK_CACHE.clear()


def _answers(monkeypatch, *pairs):
    """把 getaddrinfo 钉成给定的 (family, address) 集合。"""
    def fake(host, port, *a, **kw):
        return [(fam, 0, 0, "", (addr, 0)) for fam, addr in pairs]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


class TestTheProbe:

    def test_a_fake_ip_answer_is_named_with_its_network(self, monkeypatch):
        _answers(monkeypatch, (socket.AF_INET, "198.18.0.32"))
        verdict, addr, detail = security.probe_fake_ip()
        assert verdict == "fake-ip"
        assert addr == "198.18.0.32" and "198.18.0.0/15" in detail

    def test_a_public_answer_is_a_public_answer(self, monkeypatch):
        _answers(monkeypatch, (socket.AF_INET, "93.184.216.34"))
        assert security.probe_fake_ip()[0] == "public"
        assert security.recheck_posture_note() == ""

    def test_an_unresolvable_probe_never_relaxes_anything(self, monkeypatch):
        """离线不是放宽守卫的理由：探测不确定时保持 fail-closed。"""
        def boom(*a, **kw):
            raise OSError("no resolver")
        monkeypatch.setattr(socket, "getaddrinfo", boom)
        assert security.probe_fake_ip()[0] == "unprobed"
        assert security._dns_recheck_enabled() is True


class TestWhatTheVerdictChanges:

    def test_recheck_is_skipped_only_on_a_fake_ip_host(self, monkeypatch):
        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"),
                 (socket.AF_INET6, "fd00::20"))
        assert security._dns_recheck_enabled() is False
        note = security.recheck_posture_note()
        assert "auto-skipped" in note
        assert "198.18.0.0/15" in note and "fc00::/7" in note
        # 硬回口子必须写在用户看得见的地方，而不是只在源码注释里
        assert "DHOLE_SSRF_DNS_RECHECK on" in note

    def test_an_explicit_setting_outranks_the_environment(self, monkeypatch):
        """这是安全默认，不是一个建议：探测到 fake-ip 也不能覆盖人明确说过的 on。"""
        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))
        monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "on")
        assert security._dns_recheck_enabled() is True
        monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "0")
        assert security._dns_recheck_enabled() is False

    def test_an_unrecognised_value_still_fails_closed(self, monkeypatch):
        """typo 的判定方向不变（W4/D1）：flase 不会顺带把复检关掉。"""
        _answers(monkeypatch, (socket.AF_INET, "93.184.216.34"))
        monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "flase")
        assert security._dns_recheck_enabled() is True


class TestWhatDoesNotMove:
    """自动跳过的只是 DNS 复检这一层。其余守卫在同一种机器上必须照旧。"""

    @pytest.mark.parametrize("url,why", [
        ("http://127.0.0.1:8080/x", "loopback IP literal"),
        ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
        ("http://10.0.0.5/internal", "private range IP literal"),
    ])
    def test_ip_literals_and_metadata_stay_blocked(self, monkeypatch, url, why):
        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))   # 伪装成 fake-ip 机器
        security.reset_fake_ip_probe()
        assert security._dns_recheck_enabled() is False, "前置：这台机器已自动跳过复检"
        with pytest.raises(security.SecurityError):
            security.validate_url(url)

    def test_a_hostname_blacklisted_name_is_still_blocked(self, monkeypatch):
        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))
        with pytest.raises(security.SecurityError):
            security.validate_url("http://localhost:3000/")


class TestTheNoteReachesTheCaller:
    """跳过这件事要在回包里看得见 —— 一道悄悄放宽的安全闸门等于没有交代。"""

    def test_metadata_carries_the_note_only_when_it_fired(self, monkeypatch):
        from dhole_mcp.server import _stamp_robots_mode, ResponseModel

        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))
        assert security.recheck_posture_note()
        result = ResponseModel(url="https://example.com/", status=200, content=["x"])
        _stamp_robots_mode(result)
        assert "ssrf_recheck" in (result.metadata or {})

    def test_a_healthy_host_gets_no_extra_field(self, monkeypatch):
        from dhole_mcp.server import _stamp_robots_mode, ResponseModel

        _answers(monkeypatch, (socket.AF_INET, "93.184.216.34"))
        assert security.recheck_posture_note() == ""
        result = ResponseModel(url="https://example.com/", status=200, content=["x"])
        _stamp_robots_mode(result)
        assert "ssrf_recheck" not in (result.metadata or {})
