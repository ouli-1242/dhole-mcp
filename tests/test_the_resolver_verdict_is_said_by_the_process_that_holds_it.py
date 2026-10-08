"""批次 6d · resolver 的判词属于**那个进程**，doctor 不许替它说话。

`security.probe_fake_ip()` 的判词缓存到进程寿命结束，没有过期、也没有旋钮（17.0 注销
了 `DHOLE_SSRF_DNS_RECHECK`）。这本身是对的 —— 复查层不能在请求中途改变立场。但它带来
一句只有另一个进程答得出的话：`dhole --doctor` 里那句 "the DNS-recheck layer is skipped
automatically on this host" 从前是这样长出来的 —— doctor 自己 getaddrinfo 一次，再去问
`security._dns_recheck_enabled()`，而那问的是 **doctor 本进程**刚刚自己探的那一次。两处
必然一致，这一"核对"从未核对任何东西；两个会咬人的方向因此都印不出来：

* doctor 看到 fake-ip ⇒ 说"已自动跳过、URL 照常可用"，而服务进程缓存的是公网判定
  —— 复查层在**回答调用的那个进程**里还开着，每一个公网 URL 都被拒。
* doctor 看到公网 ⇒ return "resolves to a public answer"，而服务进程缓存着 fake-ip。

修法是把"谁测的、什么时候测的、测出什么"变成一条可读的事实：测量者用
`_record_resolver_probe` 落一份 `resolver_probe.json`，doctor 读它、点名它，两个答案
不一致时说不一致。doctor 自己不再调用会写盘的那颗探测 —— 于是"doctor 除了自己的探针
不落任何东西"那条老承诺不必开豁免（见
`tests/test_doctor_items.py::test_doctor_writes_nothing_but_its_own_probe`）。

判据：

1. 真测量才落镜像，三种判词都落（public / fake-ip 带它看见的地址 / unprobed），且一个
   进程只落一次 —— 判词本来就是一次性的，镜像跟着改会让 doctor "证实"一个持有守卫的
   进程从不适用的判词。
2. doctor 那一行读盘上的判词，不是自己本进程的自洽：两个不一致的方向都必须印得出来
   （它们从前永远印不出来）。
3. 没有镜像时说"还没有哪次抓取测过"，不许把"没人测过"写成"一切正常"（与批次 6a 同一
   条：空回执必须和没数据长得不一样）。
4. 一致时点名是哪个进程、什么时候。
5. 每一支的 detail 都量过 280 —— `_short` **保尾去头**，写长了被打掉的正好是操作员要
   认出来的那半句（`test_doctor.py` 三条用例钉的就是它）。
6. doctor 不写盘；镜像缺失或坏掉时读成 {}，并且不许抛。
7. 形状守卫：那一行里不许再有 `_dns_recheck_enabled()`。
"""

from __future__ import annotations

import ast
import inspect
import socket

import pytest

from dhole_mcp import paths
from dhole_mcp import security
from dhole_mcp import updater

FAKE_V4 = "198.18.0.32"
FAKE_V6 = "fd00::20"
PUBLIC_V4 = "93.184.216.34"
ROW_BUDGET = 280  # updater.doctor() 渲染时的 _short(detail, w=280)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """DHOLE_HOME 指到临时目录；镜像本身由 conftest 的 `_no_real_home_state_writes`
    接管到 `tmp_path/resolver_probe.json`（写与读同一条 `paths.file` 接缝，所以这里
    只需要认一个位置）。"""
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_verdict_per_test():
    """判词按进程缓存：不逐用例清一次，上一个用例的 resolver 挂具就会漏进下一个
    （用例顺序一改就闪红的那种脆弱）。"""
    security.reset_fake_ip_probe()
    yield
    security.reset_fake_ip_probe()


def _dns(monkeypatch, *answers):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, 1, 6, "", (ip, 0)) for ip in answers]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _measure(monkeypatch, *answers) -> dict:
    """跑真的生产者（一个抓取进程的第一次探测），再读它留给别的进程的那一份。"""
    _dns(monkeypatch, *answers)
    security.probe_fake_ip()
    return security.resolver_probe_record()


def _row(out: str) -> str:
    return next(line for line in out.splitlines() if "resolver reality" in line)


# ─── 1 · 测量者把判词写下来 ───


class TestTheProcessThatMeasuresLeavesAnAnswer:

    def test_a_public_measurement_is_mirrored(self, sandbox, monkeypatch):
        record = _measure(monkeypatch, PUBLIC_V4)
        assert record["verdict"] == "public"
        assert record["pid"]
        assert record["probed_at"].endswith("Z")

    def test_a_fake_ip_measurement_carries_the_address_it_saw(
            self, sandbox, monkeypatch):
        record = _measure(monkeypatch, FAKE_V4)
        assert record["verdict"] == "fake-ip"
        assert record["address"] == FAKE_V4, "doctor 要点名它看见的那个保留地址"
        # 判词是谁家给的：镜像要列出这次判定所依据的**每一颗**域名（R3刀2 起是两颗
        # 独立注册域名合取），只印一颗等于让 doctor 替一个更窄的证据说话。
        assert record["probe"] == "example.com, example.org"

    def test_a_resolver_that_answered_nothing_is_recorded_as_unprobed(
            self, sandbox, monkeypatch):
        def boom(*a, **k):
            raise socket.gaierror("name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        security.probe_fake_ip()
        assert security.resolver_probe_record()["verdict"] == "unprobed"

    def test_the_mirror_is_the_first_answer_not_the_latest(
            self, sandbox, monkeypatch):
        """The verdict is cached for the process lifetime ON PURPOSE (a guard that
        changed its mind mid-request would apply two policies to one call), so the
        mirror describes that same one-shot fact: re-probing under a resolver that
        has since flipped rewrites nothing."""
        first = _measure(monkeypatch, PUBLIC_V4)
        _dns(monkeypatch, FAKE_V4)
        security.probe_fake_ip()          # cache hit - no new measurement
        assert security.resolver_probe_record() == first


# ─── 2 · doctor 说的是那个进程的判词 ───


class TestDoctorReportsTheOtherProcess:

    def test_the_direction_that_could_never_print_before(
            self, sandbox, monkeypatch, capsys):
        _measure(monkeypatch, PUBLIC_V4)
        _dns(monkeypatch, FAKE_V4)
        assert updater.doctor() == 0, "fake-ip 是环境事实，不许把体检判红"
        out = capsys.readouterr().out
        line = _row(out)
        assert "fake-ip proxy detected" in line
        assert "measured public" in line, line
        assert "restart" in out

    def test_the_reverse_direction_prints_too(self, sandbox, monkeypatch, capsys):
        _measure(monkeypatch, FAKE_V4)
        _dns(monkeypatch, PUBLIC_V4)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        line = _row(out)
        assert "measured fake-ip" in line and FAKE_V4 in line, line
        assert "restart" in out

    def test_disagreement_is_info_not_a_red_row(self, sandbox, monkeypatch, capsys):
        _measure(monkeypatch, PUBLIC_V4)
        _dns(monkeypatch, FAKE_V4)
        updater.doctor()
        line = _row(capsys.readouterr().out)
        assert "✗" not in line, line

    def test_agreement_names_the_process_and_the_moment(
            self, sandbox, monkeypatch, capsys):
        record = _measure(monkeypatch, PUBLIC_V4)
        _dns(monkeypatch, PUBLIC_V4)
        updater.doctor()
        line = _row(capsys.readouterr().out)
        assert "the dhole process that measured it agrees" in line, line
        assert f"pid {record['pid']} at {record['probed_at']}" in line, line


# ─── 3 · 没人测过时，空要说成空 ───


class TestAnAbsentMirrorIsSaidOutLoud:

    def test_a_fresh_home_says_no_fetch_has_measured_this(
            self, sandbox, monkeypatch, capsys):
        _dns(monkeypatch, PUBLIC_V4)
        updater.doctor()
        assert "no dhole fetch has measured this resolver yet" in _row(
            capsys.readouterr().out)

    def test_the_same_words_on_the_fake_ip_side(self, sandbox, monkeypatch, capsys):
        _dns(monkeypatch, FAKE_V4)
        updater.doctor()
        line = _row(capsys.readouterr().out)
        assert "no dhole fetch has measured" in line, line
        # 这一支同时要说"会自动跳过"，因为下一行用户就会真的去抓一个 URL。
        assert "those two ranges are tolerated" in line

    def test_a_broken_mirror_reads_as_absent_and_never_raises(
            self, sandbox, monkeypatch, capsys):
        target = paths.file("resolver_probe.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{not json", encoding="utf-8")
        assert security.resolver_probe_record() == {}
        _dns(monkeypatch, PUBLIC_V4)
        checks: list = []
        updater._append_resolver_reality(checks)        # 不许抛
        assert "no dhole fetch has measured" in checks[0][2]


# ─── 4 · 行宽与"doctor 不写盘" ───


class TestTheRowFitsAndTheDoctorStaysClean:

    @pytest.mark.parametrize("answers,verdict", [
        ((PUBLIC_V4,), ""),
        ((FAKE_V4,), ""),
        ((FAKE_V4, FAKE_V6), ""),
        ((FAKE_V4, FAKE_V6), "fake-ip"),
        ((FAKE_V4,), "public"),
        ((FAKE_V4,), "unprobed"),
        ((PUBLIC_V4,), "fake-ip"),
        ((PUBLIC_V4,), "public"),
        ((PUBLIC_V4,), "unprobed"),
    ])
    def test_no_branch_relies_on_its_head_surviving(self, sandbox, monkeypatch,
                                                    answers, verdict):
        """`_short(detail, 280)` 保尾去头：写长了掉的是 "fake-ip proxy detected
        (example.com -> 198.18.0.32 …)" 这半句，正是操作员认得出来的那半句。逐支量，
        而不是量到一支没超就当整颗函数没超。"""
        if verdict:
            security._record_resolver_probe(
                verdict, FAKE_V4 if verdict == "fake-ip" else "")
        _dns(monkeypatch, *answers)
        checks: list = []
        updater._append_resolver_reality(checks)
        label, state, detail, _fix = checks[0]
        assert (label, state) == ("resolver reality", state)
        assert state != "fail", "环境事实不许把体检判红"
        assert len(detail) <= ROW_BUDGET, f"{len(detail)} 字符：{detail}"

    def test_doctor_leaves_the_mirror_it_did_not_measure_alone(
            self, sandbox, monkeypatch):
        """doctor 自己绝不写盘（`test_doctor_items` 那条"除了探针不落任何东西"），
        而它做到这一点的方式是**不再调用那颗会写的探测** —— 这里钉的正是这个方式。
        否则下一次体检读到的就是自己上一次的影子。"""
        _dns(monkeypatch, FAKE_V4)
        updater.doctor()
        assert not paths.file("resolver_probe.json").exists()

    def test_the_self_check_is_gone_from_the_call_graph(self):
        """形状守卫：这一行不许再问本进程的判定 —— 它答的是"doctor 这个马上就退出的
        进程会怎么做"，而报告那句话的主语是用户的 dhole。走 AST 而不是文本：docstring
        里正写着"从前它这么问"，文本守卫会把自己钉红。

        两个名字都守着：R3刀2 把 `_dns_recheck_enabled` 换成了 `_tunnel_exempt_engaged`，
        守卫只认旧名的话，下一次改名就正好把它变成一条假话。
        """
        tree = ast.parse(inspect.getsource(updater._append_resolver_reality))
        called = {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
        assert "_dns_recheck_enabled" not in called, called
        assert "_tunnel_exempt_engaged" not in called, called
        assert "resolver_probe_record" in called
