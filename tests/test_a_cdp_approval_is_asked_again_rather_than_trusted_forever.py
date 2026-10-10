"""一次 CDP 批准不该把三月的按键授权到十月（体验修复刀3-D2）。

缺陷的形状不是"缺一个 TTL 旋钮"。名册从前写成 `{"hosts": ["a.test", …]}` —— 裸主机名、
无时间戳，于是"这台被批准过"是一个**没有时间维度**的事实：读者既问不出"什么时候批的"，
也就想不起"还要不要撤"。这台机器上的名册里就躺着两条那样的残留，它们证明的是
**撤销不可发现**，不是"窗口该设成几天"。

这一刀把形状改成 `{host: 批准的 ISO 时间戳}`，并且做了一个刻意的选择：过窗的批准
**重新征询**，而不是静默失效。失效会把一套能用的配置变成谜样失败（"昨天还行的 attach
今天读了个空"），而再问只多花一次按键 —— 一次安全动作的代价该出在按键上，不该出在
"为什么今天不灵了"上。

因此这里钉的三件事，缺一件这一刀就只是给名册加了个没人读的字段：

1. **判活**：窗口外的记录不再等于授权，且这一判断每次现算 —— 缓存的是原始读数而不是
   判活结果，否则一个活过边界的 MCP 进程会一直认下去（文件的 mtime 并不变）。
2. **说法**：拒答要点名是三种成因里的哪一种（从未批过 / 过窗并引用日期 / 日期读不出来）。
   旧句子写死 "this is the FIRST attach"，在一台三个月前批过的主机上是错的诊断，而它会
   引导调用方去解释成"用户从没同意过"。
3. **可见**：`sys_state` 在给出撤销命令的同一口气里点名每台日期、年龄、名册文件路径；
   `dhole doctor` 不许把过窗的报成"已授权"（判活只有一份属主）。

旧形状（裸列表 / 无日期）读成"年龄读不出来"，**不**自动有效：把别人的记录悄悄盖章成
"今天批的"等于替用户补做一次他没做过的按键。它要走一次重新征询才拿到时间戳。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dhole_mcp import guide, updater
import dhole_mcp.server as server_mod
from dhole_mcp.server import (
    MasterFetchServer,
    _cdp_attach_gate,
    _cdp_authorized_hosts,
    _cdp_hosts_file,
    _cdp_records,
    _cdp_write_records,
)

CDP = "http://127.0.0.1:9222"
HOST = "a.example.test"
SITE = "https://a.example.test/1"
OTHER = "b.example.test"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _roster_path() -> pathlib.Path:
    """conftest 把 home 指到每条用例的 tmp_path，所以这里问的是"这台进程的名册在哪"。"""
    return pathlib.Path(_cdp_hosts_file())


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """doctor 体检的是它写得进的那个 home（与 tests/test_doctor.py 同一口径）。"""
    home = tmp_path / "home"
    monkeypatch.setenv("DHOLE_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _fresh_roster():
    """每条用例从空名册开始，并且**忘掉进程内读数**。

    名册的缓存形状（`{host: 时间戳}`）是 server 那一层的实现细节；挂具去抄它一个字面量
    就等于把实现写进测试 —— 抄错的那次不会红，只会读到一个假名册。
    """
    _roster_path().unlink(missing_ok=True)
    server_mod._cdp_forget_roster()
    yield
    server_mod._cdp_forget_roster()


def _gate(confirm: bool = False, targets=(SITE,)):
    return asyncio.run(_cdp_attach_gate(CDP, confirm=confirm, targets=list(targets)))


def _sys_state(**kwargs):
    return asyncio.run(MasterFetchServer().sys_state(**kwargs))


def _cause_of(error: str) -> str:
    """拒答里那句"为什么再问"，截掉两边重复的固定文案。"""
    return error.split("nothing was executed - ")[1].split(". Attaching")[0]


class TestAnApprovalHasAnAge:

    def test_a_press_writes_a_date_not_a_bare_name(self):
        error, allowed = _gate(confirm=True)
        assert error is None and allowed == frozenset({HOST})
        raw = json.loads(_roster_path().read_text(encoding="utf-8"))
        assert list(raw["hosts"]) == [HOST], "授权的对象是目标主机，不是调试端点"
        stamp = raw["hosts"][HOST]
        assert stamp[:10] == _today(), f"批准没盖上今天的戳: {stamp!r}"
        # 第二次调用不该再问：窗口内的一次按键就是一次授权
        again_error, again_allowed = _gate()
        assert again_error is None and HOST in again_allowed

    def test_an_approval_past_the_window_is_asked_again(self):
        _cdp_write_records({HOST: _iso(server_mod._CDP_APPROVAL_DAYS + 15)})
        assert _cdp_authorized_hosts() == frozenset(), "过窗的批准还算授权"
        error, allowed = _gate()
        assert allowed is None and error is not None
        assert "re-asked" in error and str(server_mod._CDP_APPROVAL_DAYS) in error

    def test_an_approval_inside_the_window_is_not_re_asked(self):
        """窗口内重新征询就是把这个功能变成谜样失败 —— 半边对照，缺了它上面那条
        可以靠"永远拒"通过。"""
        _cdp_write_records({HOST: _iso(server_mod._CDP_APPROVAL_DAYS - 2)})
        error, _ = _gate()
        assert error is None

    @pytest.mark.parametrize(
        "records,needle,why",
        [({}, "has never been approved", "名册里没有这台"),
         ({HOST: _iso(45)}, "was approved on", "过窗的那台要带日期"),
         ({HOST: ""}, "before approvals carried a date", "旧形状读不出年龄"),
         ({HOST: "tomorrow"}, "before approvals carried a date", "坏戳同属读不出")],
    )
    def test_the_refusal_names_which_of_the_three_causes_it_is(self, records, needle, why):
        """三种成因各有各的话 —— 共用一句 "FIRST attach" 会给出错的解法。

        "从未批过"的正解是请用户按一次；"过窗"的正解是**再**请一次并说明为什么又要问；
        "读不出日期"的正解是告诉他这台是从前那份没有日期的名册继承来的。把三者说成
        同一个"第一次"，读者就会以为自己从没批过，而不是去查那颗过期。
        """
        if records:
            _cdp_write_records(records)
        error, _ = _gate()
        assert error is not None and needle in error, f"{why}：{(error or '')[:220]}"

    def test_the_three_causes_do_not_share_one_sentence(self):
        """三句必须互不相同：同一条文案覆盖三种成因，等于诊断没做区分。"""
        said = []
        for records in ({}, {HOST: _iso(45)}, {HOST: ""}):
            _roster_path().unlink(missing_ok=True)
            server_mod._cdp_forget_roster()
            if records:
                _cdp_write_records(records)
            error, _ = _gate()
            said.append(_cause_of(error))
        assert len(set(said)) == 3, f"三种成因说成了同一句话: {said}"
        assert "45" in said[1] or _iso(45)[:10] in said[1], "过窗那句没引用批准日期"

    def test_a_legacy_list_roster_is_re_asked_then_carries_a_date(self):
        """旧磁盘形状（裸列表）读成"无日期"，再按一次才升级成带戳的形状。"""
        _roster_path().write_text(json.dumps({"hosts": [HOST]}), encoding="utf-8")
        server_mod._cdp_forget_roster()
        assert _cdp_records() == {HOST: ""}
        assert _cdp_authorized_hosts() == frozenset(), \
            "把旧名册悄悄盖章成今天批的，就是替用户补做一次他没按过的键"
        error, _ = _gate()
        assert error is not None and "before approvals carried a date" in error
        assert _gate(confirm=True)[0] is None
        raw = json.loads(_roster_path().read_text(encoding="utf-8"))
        assert isinstance(raw["hosts"], dict) and raw["hosts"][HOST][:10] == _today()
        assert _gate()[0] is None, "升级后又被当成过期，那这台永远问不完"

    def test_the_second_press_refreshes_the_stamp_instead_of_stacking(self):
        _cdp_write_records({HOST: _iso(90)})
        assert _gate(confirm=True)[0] is None
        fresh = _cdp_records()[HOST]
        assert fresh[:10] == _today(), f"重新批准后戳没刷新: {fresh!r}"
        assert len(_cdp_records()) == 1, "一台主机在名册里不该有两行"

    def test_liveness_is_recomputed_even_when_the_file_never_changes(self, monkeypatch):
        """一个活过边界的进程不许继续认那条批准 —— 哪怕文件的 mtime 一格没动。

        这里把窗口调窄而不是调时钟：等价于"墙上钟走过了边界"，而磁盘上那个字节序列
        一个字都没变。如果有人把**判活结果**而不是原始读数存进缓存，这一条就会红。
        """
        import pathlib as _p

        frozen = SimpleNamespace(st_mtime=1.0, st_size=100)
        real_stat = _p.Path.stat

        def fake_stat(self, *a, **k):
            if self.name == "cdp_hosts.json":
                return frozen
            return real_stat(self, *a, **k)

        monkeypatch.setattr(_p.Path, "stat", fake_stat)
        _cdp_write_records({HOST: _iso(5)})
        assert HOST in _cdp_authorized_hosts()
        _cdp_records()                       # 让缓存热起来
        monkeypatch.setattr(server_mod, "_CDP_APPROVAL_DAYS", 3)
        assert _cdp_authorized_hosts() == frozenset(), \
            "缓存了判活结果：文件没变就一直认这条已过窗的批准"
        assert _gate()[0] is not None, "闸门还在替这个进程记住旧结论"


class TestTheRosterIsReadableFromTheAgentSide:

    def test_the_census_names_age_path_and_the_revoke_command(self):
        _cdp_write_records({HOST: _iso(9)})
        out = _sys_state(show=["cdp_hosts"])
        assert out.cdp_hosts == [HOST]
        breath = " ".join([out.message, out.next_action])
        assert "9 day(s) ago" in breath, out.next_action
        assert _cdp_hosts_file() in breath, "读不出文件在哪，撤销就只能靠猜"
        assert "clear=['cdp:host=" in breath and "re-asked" in breath

    def test_the_field_only_lists_hosts_the_gate_would_admit(self):
        """`cdp_hosts` 的契约是"现在 attach 会放行的是谁"，不是"文件里写过谁"。"""
        _cdp_write_records({HOST: _iso(9), OTHER: _iso(60)})
        out = _sys_state(show=["cdp_hosts"])
        assert out.cdp_hosts == [HOST], "把过窗的报成已批准，字段就和闸门说反话"
        assert OTHER in out.message, "但也不能当它不在文件里"
        assert "NOT approved right now" in out.message

    def test_an_out_of_window_host_can_still_be_revoked(self):
        """撤一台"躺在磁盘上但已过窗"的主机是真动作，不是 no-op。"""
        _cdp_write_records({HOST: _iso(9), OTHER: _iso(60)})
        out = _sys_state(clear=[f"cdp:host={OTHER}"], confirm=True)
        assert "Nothing matches" not in (out.error or ""), out.error
        assert "1 CDP attach approval(s) revoked" in out.message
        assert OTHER not in _cdp_records()

    def test_the_revoke_receipt_says_who_is_left_and_where(self):
        _cdp_write_records({HOST: _iso(2), OTHER: _iso(20)})
        out = _sys_state(clear=[f"cdp:host={OTHER}"], confirm=True)
        breath = " ".join([out.message, out.next_action])
        assert f"{HOST} (approved" in breath and _cdp_hosts_file() in breath, out.message
        # 撤销成功却同时记一句"某个存储拒绝了"，是同一口气里的自相矛盾：读的人
        # 分不清到底撤没撤。这条也是那次 `report.add` 写在未定义名字上时的捕手。
        assert not out.error, f"成功的撤销回执里记了一次存储失败: {out.error}"

    def test_clearing_the_whole_roster_drops_aged_entries_too(self):
        """`clear=['cdp_hosts']` 清的是磁盘，不是"此刻还活着的那半"。"""
        _cdp_write_records({HOST: _iso(60), OTHER: _iso(1)})
        out = _sys_state(clear=["cdp_hosts"], confirm=True)
        assert "2 CDP attach approval(s) revoked" in out.message, out.message
        assert not out.error, out.error
        assert _cdp_records() == {}
        assert not _roster_path().exists(), "撤完还剩一行读不出来的记录"


class TestDoctorDoesNotContradictTheGate:
    """`dhole doctor` 从前自己抄了一遍名册算术，于是它会把过窗的算成"已授权"。

    两句相反的话由两个人各自维护，正是这个仓库反复记账的那种漂移：判活只有一份属主。
    """

    def _fence_lines(self, capsys):
        updater.doctor()
        out = capsys.readouterr().out
        return "\n".join(line for line in out.splitlines()
                         if "cdp fence" in line or "cdp approval" in line
                         or "fix:" in line and "cdp_hosts" in line)

    def test_an_aged_approval_is_not_reported_as_authorized(self, isolated_home, capsys):
        _cdp_write_records({HOST: _iso(45)})
        line = self._fence_lines(capsys)
        assert "authorized for 0 host" in line, line
        assert "outside the window" in line and HOST in line, line

    def test_a_live_approval_reports_its_count_and_its_age(self, isolated_home, capsys):
        _cdp_write_records({HOST: _iso(4)})
        line = self._fence_lines(capsys)
        assert "1 host" in line and HOST in line, line
        assert "4 day(s) ago" in line, line
        assert _cdp_hosts_file() in line, "名册路径要出现在体检报告里，撤销才有落脚点"


class TestTheManualCarriesTheWindow:
    """文档形状：过窗要重新征询是**用户**要预见的事，而拒答里的解释只在那一次出现。"""

    NEEDLES = {
        "SKILL.md": ["30 days", "cdp_authorization_required"],
        "references/troubleshooting.md": ["more than 30 days ago", "age in days",
                                          "approval date"],
        "references/configuration.md": ["good for 30 days", "asked again",
                                        "with its timestamp"],
        "references/tools.md": ["re-asked after 30 days"],
        "references/examples.md": ["younger than 30 days"],
    }

    @pytest.mark.parametrize("rel,needles", list(NEEDLES.items()))
    def test_the_window_is_written_where_the_caller_reads_it(self, rel, needles):
        text = (guide._ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{rel} 没写着「{needle}」"

    def test_the_corpus_no_longer_calls_it_a_first_attach(self):
        """"FIRST attach" 作为唯一成因已经从代码里消失了，文档不许还这么说。"""
        for rel in list(self.NEEDLES) + ["references/fields.md", "references/recipes.md"]:
            text = (guide._ROOT / rel).read_text(encoding="utf-8")
            assert "FIRST attach" not in text and "first attach" not in text, rel

    def test_the_wire_names_the_window_too(self):
        """常驻层那句 "first-time sensitive action" 在第二次征询的按键上是假话。"""
        defs = {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}
        confirm = defs["web_read"]["inputSchema"]["properties"]["confirm"]["description"]
        assert "younger than 30 days" in confirm, confirm
        assert "first-time" not in confirm and "FIRST" not in confirm
        for tool in ("web_read", "web_act"):
            cdp = defs[tool]["inputSchema"]["properties"]["cdp_url"]["description"]
            assert "30 days" in cdp, f"{tool}: {cdp}"
