"""fake-IP 环境自测自适配（W12）。

这个默认值曾经是新手的第一道墙：Clash / sing-box 的 TUN + fake-ip 解析器给**每一
个公网域名**都返回保留地址，于是 fail-closed 的 DNS 复检把整台机器判成内网，
web_read / web_search 全部失败 —— 而唯一的出路是**事先知道**要设
`DHOLE_SSRF_DNS_RECHECK=0`。README 里那句"零配置即可用"在这台机器上是被实测证伪
的（见 CHANGELOG W3）。

现在改由环境自己回答：探测到 fake-ip 就豁免**那两段地址**（198.18.0.0/15、fc00::/7），
豁免这件事在回包里可见。17.0 连那条硬回的路也收掉了 —— 旋钮注销，判定只由探测结论
给出。R3刀2 把口径又收紧一格：判成隧道**不再让整层停摆**，复验照跑，只是那两段不算
内网证据；下面每一条都守在"被放宽的只有这两段"这一侧。
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
    """把 getaddrinfo 钉成给定的 (family, address) 集合（每颗探测名同一份答案）。"""
    def fake(host, port, *a, **kw):
        return [(fam, 0, 0, "", (addr, 0)) for fam, addr in pairs]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _answers_per_name(monkeypatch, table):
    """按名字给不同答案 —— 合取判定与隧道自愈都要看"哪一颗域名答了什么"。"""
    def fake(host, port, *a, **kw):
        pairs = table.get(host, [])
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
        assert security._tunnel_exempt_engaged() is False


class TestWhatTheVerdictChanges:

    def test_a_tunnel_verdict_tolerates_the_two_ranges_not_the_layer(self, monkeypatch):
        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"),
                 (socket.AF_INET6, "fd00::20"))
        assert security._tunnel_exempt_engaged() is True
        note = security.recheck_posture_note()
        assert "tolerates the fake-ip ranges" in note
        assert "198.18.0.0/15" in note and "fc00::/7" in note
        # 只放宽那两段、复验本身照跑 —— 这件事也必须写在用户看得见的地方
        assert "The recheck itself still runs" in note
        # 17.0：旋钮已注销，判定只来自探测 —— 文案里不许再指一条不存在的出路
        assert "DHOLE_SSRF_DNS_RECHECK" not in note

    def test_the_retired_knob_moves_nothing(self, monkeypatch):
        """``DHOLE_SSRF_DNS_RECHECK`` 注销后设什么都不改变判定，也不报错。

        两个方向都要钉住：公网机器上设 ``0`` 不许把复检关掉（一道 fail-closed
        的守卫不能被一个没人再读的名字松动），fake-ip 机器上设 ``on`` 也不许把
        它强开回去（那恰好是让这台机器所有抓取全挂的值）。typo 同理。
        """
        _answers(monkeypatch, (socket.AF_INET, "93.184.216.34"))
        for value in ("0", "off", "flase", "on"):
            monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", value)
            assert security._tunnel_exempt_engaged() is False, value

        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))
        security.reset_fake_ip_probe()
        monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "on")
        assert security._tunnel_exempt_engaged() is True


class TestWhatDoesNotMove:
    """自动豁免的只有隧道那两段。其余守卫在同一种机器上必须照旧。"""

    @pytest.mark.parametrize("url,why", [
        ("http://127.0.0.1:8080/x", "loopback IP literal"),
        ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
        ("http://10.0.0.5/internal", "private range IP literal"),
    ])
    def test_ip_literals_and_metadata_stay_blocked(self, monkeypatch, url, why):
        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))   # 伪装成 fake-ip 机器
        security.reset_fake_ip_probe()
        assert security._tunnel_exempt_engaged() is True, "前置：这台机器已被判成隧道"
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


class TestTheNoteIsSaidOncePerCall:
    """一条进程事实不该在 20 行里说 20 遍。

    2026-10-05 实测：`web_read(urls=[20 颗])` 把同一段 273–361 字的判词印了 20 份，
    一次调用 5.5–7.2KB 的样板。它说的是"这台机器的解析器跳过了重查"，与页面无关，
    所以它在信封上有一份就够了。两条边界一起守：不许把**不一致**的判词折成一份
    （那是关于安全的一句假话），也不许让Healthy 机器多付一个字段。
    """

    @staticmethod
    def _rows(monkeypatch, notes):
        from dhole_mcp.server import ResponseModel, _stamp_robots_mode

        out = []
        for i, note in enumerate(notes):
            r = ResponseModel(url=f"https://a.test/{i}", status=200, content=["x"],
                              metadata={})
            _stamp_robots_mode(r)
            if note is not None:
                r.metadata["ssrf_recheck"] = note
            out.append(r)
        return out

    def test_an_identical_note_across_rows_becomes_one_field_on_the_envelope(
            self, monkeypatch):
        from dhole_mcp.server import BulkResponseModel
        from dhole_mcp.wire import _wire_dump

        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))
        note = security.recheck_posture_note()
        assert note, "前置：这台机器要被认成 fake-ip"
        bulk = BulkResponseModel(results=self._rows(monkeypatch, [note] * 4),
                                 total=4, successful=4)
        wire = _wire_dump(bulk)
        assert wire.get("ssrf_recheck") == note, sorted(wire)
        for row in wire["results"]:
            assert "ssrf_recheck" not in (row.get("metadata") or {}), row
        # 省下的必须是真字节，不是"换了个地方放同样的字"。
        assert len(_wire_dump(bulk)) < 4 * len(note)

    def test_rows_that_disagree_are_not_folded_into_one_statement(
            self, monkeypatch):
        """两种判词折成一份 = 关于这道闸门的假话。宁可每行各说各的。"""
        from dhole_mcp.server import BulkResponseModel
        from dhole_mcp.wire import _wire_dump

        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))
        note = security.recheck_posture_note()
        rows = self._rows(monkeypatch, [note, "a different verdict"])
        wire = _wire_dump(BulkResponseModel(results=rows, total=2, successful=2))
        assert "ssrf_recheck" not in wire, "不一致却被折成了一句"
        assert [(r.get("metadata") or {}).get("ssrf_recheck")
                for r in wire["results"]] == [note, "a different verdict"]

    def test_a_crawl_states_the_posture_at_the_level_the_caller_reads(
            self, monkeypatch):
        """爬取的每页回执是内部物，从来没上线 —— 所以这道判词以前无处可说。"""
        from dhole_mcp.crawl import CrawlResponseModel
        from dhole_mcp.wire import _wire_dump

        _answers(monkeypatch, (socket.AF_INET, "198.18.0.9"))
        note = security.recheck_posture_note()
        c = CrawlResponseModel(start_url="https://a.test", pages=[],
                               ssrf_recheck=note)
        assert _wire_dump(c).get("ssrf_recheck") == note
        healthy = CrawlResponseModel(start_url="https://a.test", pages=[])
        assert "ssrf_recheck" not in _wire_dump(healthy)
