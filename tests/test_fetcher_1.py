"""test_fetcher_1.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
import sys
import pytest
from dhole_mcp import updater
import asyncio
import http.server
import re
import socket
import threading
from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer


# ============================================================================
# 来源：test_doctor_items.py
# ============================================================================
# B2-5 · `dhole --doctor` 的计划 12 项。

# 12 项各自必须出现的那一行标签。少一项 = 装机者少一个答案。
REQUIRED_ROWS = (
    "python + platform",      # 1
    "dependency layers",      # 2
    "chromium binary",        # 3
    "chromium launch",        # 3b · 真起一次；套件里是"被挂具跳过"那一行
    "rerank model",           # 4
    "stdio handshake",        # 5
    "config",                 # 6 · 两扇门（设了东西时是逐变量行）
    "robots posture",         # 8
    "search proxy",           # 9
    "live probe",             # 10 · 套件里是"被挂具跳过"那一行
    "state dir contents",     # 11
    "connect cost",           # 12
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DHOLE_HOME", str(home))
    return home


class TestTheTwelveItems:

    def test_every_item_prints_its_own_row(self, isolated_home, capsys):
        updater.doctor()
        out = capsys.readouterr().out
        missing = [label for label in REQUIRED_ROWS if label not in out]
        assert not missing, f"doctor 少了这几项：{missing}"

    def test_the_item_scan_reacts_to_an_item_that_stopped_reporting(
            self, isolated_home, monkeypatch, capsys):
        """机制自检：上一条的全部价值在于"少一行真的会红"。这里让第 12 项什么都不
        印，同一段断言必须失败 —— 否则它只是在读自己的假设。"""
        monkeypatch.setattr(updater, "_append_wire_cost", lambda checks: None)
        updater.doctor()
        out = capsys.readouterr().out
        assert "connect cost" not in out, "打桩没生效，上一条断言是空的"
        missing = [label for label in REQUIRED_ROWS if label not in out]
        assert missing == ["connect cost"], missing

    def test_the_two_network_rows_say_they_were_skipped(self, isolated_home, capsys):
        """挂具关掉的两项必须显式说出来：静默少两行的诊断命令，会让人以为这两项
        本来就不存在。"""
        updater.doctor()
        out = capsys.readouterr().out
        assert "skipped by the test harness" in out, out
        assert "live probe" in out, out

    def test_doctor_writes_nothing_but_its_own_probe(self, isolated_home):
        """除了 .doctor-probe（写完即删），doctor 不许在状态目录里留下任何东西：
        它是"坏了才跑"的命令，跑完不该改变现场。

        17.0 批次 1 给这一条开了一个**有名字的豁免**：3b 那项探针会真起一次浏览器，
        起不来的时候它必须把这一笔写进 `browser_install.json` 的 `launch` 段 —— 否则
        "盘上有、起不来"这一态只有这一次运行的现场知道，下次没人记得。豁免的确切边界
        （成功时一个字都不写、失败时只写那一段）由
        `tests/test_the_version_panel_does_not_claim_a_browser_it_cannot_launch.py`
        最后那对用例钉着；这一条继续钉默认形状（套件里 live 项是关的，探针不参与）。
        """
        updater.doctor()
        assert not isolated_home.exists() or list(isolated_home.iterdir()) == []


class TestItem1Runtime:

    def test_an_ancient_python_fails_and_names_the_fix(self, isolated_home,
                                                       monkeypatch, capsys):
        monkeypatch.setattr(sys, "version_info", (3, 10, 0), raising=False)
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "python + platform" in out
        assert "CPython >= 3.11" in out

    def test_the_platform_is_named_even_when_it_is_fine(self, isolated_home, capsys):
        updater.doctor()
        line = next(x for x in capsys.readouterr().out.splitlines()
                    if "python + platform" in x)
        assert any(word in line for word in ("win32", "linux", "darwin")), line


class TestItem2DependencyLayers:

    def test_a_missing_tier_names_the_package(self, isolated_home, monkeypatch, capsys):
        monkeypatch.setattr(updater, "_has_module",
                            lambda name: name != "trafilatura")
        updater.doctor()
        out = capsys.readouterr().out
        assert "http tier needs trafilatura" in out, out

    def test_an_alternative_package_counts_as_present(self, isolated_home,
                                                      monkeypatch, capsys):
        """OCR 有两个发行名（v3 的 rapidocr、legacy 的 rapidocr_onnxruntime），PDF
        有两个后端。槽位是 OR：按 AND 判定会把装全的机器报成缺层。"""
        monkeypatch.setattr(updater, "_has_module", lambda name: name != "rapidocr")
        updater.doctor()
        out = capsys.readouterr().out
        assert "all five tiers present" in out, out

    def test_a_missing_core_dep_is_a_fail_not_a_note(self, isolated_home,
                                                     monkeypatch, capsys):
        monkeypatch.setattr(updater, "_has_module", lambda name: name != "mcp")
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "core dependencies" in out and "mcp" in out


class TestItem3Chromium:
    """第 3 项在 B3 之后读的是 driver 的清单，不是磁盘上恰好有什么目录。

    B2-5 那两条用例只设了 `PLAYWRIGHT_BROWSERS_PATH` 并造一个 `chromium-1243` 目录，
    当时的实现照目录名报"已就绪"。现在的实现问 browsers.json 要期望修订号，再问
    **这次真的会 launch 的那一个** artifact（判据是显式 channel，见
    `browser._ARTIFACT_DIRS` 那段实测），所以挂具照那个形状造。
    """

    REV = 1228

    @pytest.fixture
    def browsers(self, monkeypatch, tmp_path):
        """一个干净的浏览器世界：清单说 1228，磁盘上什么都没有，系统 Chrome 不算数。"""
        from dhole_mcp import browser

        browser.reset_install_state()
        pkg = tmp_path / "patchright_pkg"
        (pkg / "driver" / "package").mkdir(parents=True)
        (pkg / "driver" / "package" / "browsers.json").write_text(
            json.dumps({"browsers": [{"name": "chromium", "revision": self.REV}]}),
            encoding="utf-8")
        monkeypatch.setattr(browser, "_package_dir", lambda: pkg)
        monkeypatch.setattr(browser, "_detect_chrome_channel", lambda: "chromium")
        root = tmp_path / "ms-playwright"
        root.mkdir()
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(root))
        return root

    def _install(self, root, rev, kinds=("chromium", "headless_shell")):
        from dhole_mcp import browser
        for kind in kinds:
            d = root / browser._ARTIFACT_DIRS[kind].format(rev=rev)
            d.mkdir(parents=True, exist_ok=True)
            (d / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")

    def _row(self, capsys):
        return next(x for x in capsys.readouterr().out.splitlines()
                    if "chromium binary" in x)

    def test_an_absent_binary_says_it_will_self_install(self, isolated_home,
                                                       browsers, monkeypatch,
                                                       capsys):
        monkeypatch.setenv("DHOLE_HOME", str(isolated_home))
        updater.doctor()
        line = self._row(capsys)
        assert "193MB" in line, line
        assert "first" in line, line
        assert str(self.REV) in line, line
        # 说的是"这次要用的那个不在"，不是一个笼统的"浏览器没装"
        assert "chromium-1228 not installed" in line, line

    def test_a_present_binary_is_read_as_the_revision_the_driver_wants(
            self, isolated_home, browsers, monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_HOME", str(isolated_home))
        self._install(browsers, self.REV)
        updater.doctor()
        line = self._row(capsys)
        assert "chromium-1228 (revision 1228)" in line, line

    def test_only_the_shell_on_disk_is_not_read_as_ready(self, isolated_home,
                                                         browsers, monkeypatch,
                                                         capsys):
        """验收真撞过的形状：`patchright install chromium --only-shell` 装完，盘上有
        chromium_headless_shell-1228，而 dhole 传的是显式 channel='chromium' —— 那一份
        它不 launch。报"已就绪"就是替一次必然失败的启动作保；只说"没装"又藏起了用户
        已经花掉的那 119MB，所以两边都要出现在同一行里。
        """
        monkeypatch.setenv("DHOLE_HOME", str(isolated_home))
        self._install(browsers, self.REV, kinds=("headless_shell",))
        updater.doctor()
        line = self._row(capsys)
        assert "chromium-1228 not installed" in line, line
        assert "chromium_headless_shell-1228" in line, line
        assert "not launched" in line, line

    def test_an_other_revision_on_disk_is_not_reported_as_ready(
            self, isolated_home, browsers, monkeypatch, capsys):
        """pip 升过 patchright 之后磁盘上会有一个更高的 chromium-XXXX 目录，driver 却不
        认它。把它读成"已就绪"就是让 doctor 替一次必然失败的 launch 作保。"""
        monkeypatch.setenv("DHOLE_HOME", str(isolated_home))
        self._install(browsers, self.REV + 71)
        updater.doctor()
        line = self._row(capsys)
        assert "not installed" in line, line
        # 钉的是"永不点名另一颗 artifact"，不是"这行里不许出现那个整数"：这一行现在要
        # 打印它搜过的绝对目录，而临时目录名里就带着数字（pytest-1299 之类），纯整数
        # 断言迟早被自己的挂具误红。
        assert f"chromium-{self.REV + 71}" not in line, line


class TestItem4RerankModel:

    class _Model:
        def __init__(self, digest):
            self.name = "bge-zh"
            self.label = "bge cross-encoder"
            self.approx_bytes = 279_000_000
            self.min_bytes = 1_000_000
            self.publisher_sha256 = digest

    def _patch(self, monkeypatch, tmp_path, *, downloaded, digest):
        """`downloaded=True` 现在写的是一份**完整**的模型：两颗文件齐、onnx 过 `min_bytes`。

        R02-F34 之前这个挂具只写一颗 5,000 字节的 `model.onnx` 就要求体检行报 `ok` ——
        那正是缺陷本身：判决当时由体检行自己数文件（既不看 `tokenizer.json` 也不看大小）。
        现在判决只有一位作者（`reranker.model_present()`），挂具要「装好了」就得真装好。
        **三条断言一字未改** —— 它们守的是「发布方摘要」与「只有本机自算 hash」那句区别，
        这一句在本刀同一侧；这里补上的只是那两个真实注册表对象本来就有的字段。
        半装与截断那两格的新行为由
        `tests/test_the_reranker_model_state_is_askable_on_the_wire.py` 正面钉住。
        """
        from dhole_mcp import reranker

        d = tmp_path / "models" / "bge-zh"
        d.mkdir(parents=True)
        if downloaded:
            (d / "model.onnx").write_bytes(b"x" * 1_000_010)
            (d / "tokenizer.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(reranker, "active_model",
                            lambda: self._Model(digest))
        monkeypatch.setattr(reranker, "active_model_dir", lambda: d)

    def test_not_downloaded_yet_is_a_fact_with_the_size(self, isolated_home,
                                                       monkeypatch, tmp_path,
                                                       capsys):
        self._patch(monkeypatch, tmp_path, downloaded=False, digest={})
        updater.doctor()
        out = capsys.readouterr().out
        assert "bge-zh not downloaded" in out and "279MB" in out, out

    def test_the_digest_authority_is_stated(self, isolated_home, monkeypatch,
                                            tmp_path, capsys):
        self._patch(monkeypatch, tmp_path, downloaded=True,
                    digest={"model.onnx": "abc"})
        updater.doctor()
        assert "publisher digest for 1 file(s)" in capsys.readouterr().out

    def test_self_hash_only_is_not_called_a_publisher_check(self, isolated_home,
                                                            monkeypatch, tmp_path,
                                                            capsys):
        """没有发布方摘要时，"校验过"不等于"是作者发布的那份"（离线拷模型是真场景），
        这一行不许把前者说成后者。"""
        self._patch(monkeypatch, tmp_path, downloaded=True, digest={})
        updater.doctor()
        out = capsys.readouterr().out
        assert "self-hash only" in out, out
        assert "publisher digest for" not in out, out


class TestItem5HostHandshake:

    class _Writer:
        def write(self, data):
            pass

        def flush(self):
            pass

    class _Reader:
        def __init__(self, lines):
            self._it = iter(lines)

        def readline(self):
            return next(self._it, b"")

    class _Proc:
        def __init__(self, lines):
            self.stdin = TestItem5HostHandshake._Writer()
            self.stdout = TestItem5HostHandshake._Reader(lines)
            self.killed = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    def _run(self, monkeypatch, lines):
        import subprocess as _sp

        proc = self._Proc(lines)
        monkeypatch.setattr(_sp, "Popen", lambda *a, **k: proc)
        monkeypatch.setattr(updater, "_HANDSHAKE_BUDGET_S", 0.3)
        checks: list = []
        updater._append_host_handshake(checks)
        return {c[0]: c for c in checks}["stdio handshake"], proc

    def test_a_real_handshake_reports_the_advertised_tool_count(self, monkeypatch):
        import json

        answers = [
            (json.dumps({"jsonrpc": "2.0", "id": 1,
                         "result": {"capabilities": {}}}) + "\n").encode(),
            (json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                {"name": "web_read"}, {"name": "web_search"}]}})
             + "\n").encode(),
        ]
        row, proc = self._run(monkeypatch, answers)
        assert row[1] == "ok", row
        assert "2 tools advertised" in row[2], row
        assert proc.killed, "子进程必须收掉，否则 doctor 留下一个卡住的服务器"

    def test_a_silent_server_is_a_failure(self, monkeypatch):
        """起得来却不回答，正是"宿主配置写错"的形状 —— 不许读成健康。"""
        row, _proc = self._run(monkeypatch, [])
        assert row[1] == "fail", row
        assert "no tools/list answer" in row[2], row


class TestItem6TwoDoors:
    """6 · 两扇门：宿主 env 块赢过 ~/.dhole/config.json，而这句话必须印出来。

    这里写的是真的 config.json 而不是打桩 `file_entries` —— 遮蔽关系是"value_of 到底
    从哪一扇门读到了值"，打桩读侧函数等于把要验的东西本身替掉。
    """

    def _write_config(self, value: str) -> None:
        """写真的 config.json（conftest 已把这条路径指到本用例的 tmp_path）。"""
        import json

        from dhole_mcp import env_registry

        path = env_registry.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"DHOLE_DEFAULT_CONTENT_CHARS": value}), encoding="utf-8")

    def test_env_wins_and_says_so(self, isolated_home, monkeypatch, capsys):
        self._write_config("20000")
        monkeypatch.setenv("DHOLE_DEFAULT_CONTENT_CHARS", "8000")
        updater.doctor()
        out = capsys.readouterr().out
        assert "env SHADOWS the value in ~/.dhole/config.json" in out, out
        assert "20000" not in out, out

    def test_a_file_only_value_says_where_it_came_from(self, isolated_home,
                                                      monkeypatch, capsys):
        self._write_config("20000")
        monkeypatch.delenv("DHOLE_DEFAULT_CONTENT_CHARS", raising=False)
        updater.doctor()
        out = capsys.readouterr().out
        assert "20000" in out, out
        assert "(from ~/.dhole/config.json)" in out, out


class TestItem8Robots:

    def _line(self, monkeypatch, capsys, value):
        if value is None:
            monkeypatch.delenv("DHOLE_IGNORE_ROBOTS", raising=False)
        else:
            monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", value)
        updater.doctor()
        return next(line for line in capsys.readouterr().out.splitlines()
                    if "robots posture" in line)

    def test_a_process_waiver_is_shown_as_a_warning_not_a_pass(self, isolated_home,
                                                              monkeypatch, capsys):
        line = self._line(monkeypatch, capsys, "1")
        assert "waived process-wide" in line, line

    def test_compliance_still_says_what_this_command_cannot_see(self, isolated_home,
                                                               monkeypatch, capsys):
        """绿色一行最容易被读成"一直在合规"。进程级只看得见两种状态，第三种
        （读不到 robots 所以根本没查）必须同句说出来。"""
        line = self._line(monkeypatch, capsys, None)
        assert "compliance ON" in line, line
        assert "not checked is not complied" in line, line


class TestItem9ProxyTcp:

    def _row(self, monkeypatch, reachable):
        import dhole_mcp.fetcher as fetcher

        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "http://alice:s3cret@corp:3128")
        monkeypatch.setattr(
            fetcher, "proxy_preflight",
            lambda p, t: (reachable, "" if reachable else "proxy_unreachable"))
        checks: list = []
        updater._append_proxy_tcp(checks)
        return {c[0]: c for c in checks}["search proxy tcp"]

    def test_a_dead_proxy_is_a_failure_with_a_way_out(self, monkeypatch):
        row = self._row(monkeypatch, False)
        assert row[1] == "fail", row
        assert "DID NOT ACCEPT" in row[2], row
        assert "DHOLE_SEARCH_PROXY" in row[3], row

    def test_credentials_never_reach_the_terminal(self, monkeypatch):
        """这一行把出口打印在终端上，而出口 URL 常带 user:pass；bug report 会把它
        们贴到公网上。"""
        for reachable in (True, False):
            row = self._row(monkeypatch, reachable)
            assert "s3cret" not in row[2] and "alice" not in row[2], row
            assert "***@corp:3128" in row[2], row


class TestItem10LiveProbe:

    class _Srv:
        """挂具服务器 —— 但它**照抄真实分发器的参数名闸门**。

        这一格曾经用「什么都收」的挂具打过桩，于是 doctor 的 live search 行带着
        已删除的袋形参数在套件里全绿、在真机上失败（B4 拆平那一刀实测撞到）。
        挂具接受真实的名字集合，形状写错就会红在这里，而不是红在用户机器上。
        """

        def __init__(self, results):
            self._results = results

        async def _dispatch(self, name, args):
            from dhole_mcp.server import _TOP_LEVEL_ARGS

            allowed = _TOP_LEVEL_ARGS[name]
            unknown = sorted(k for k, v in args.items()
                             if v is not None and k not in allowed)
            if unknown:
                raise ValueError(f"Unsupported argument(s) for {name}: {unknown}")
            return self._results[name]

    def _run(self, monkeypatch, results):
        import dhole_mcp.server as srv_mod

        monkeypatch.setattr(srv_mod, "MasterFetchServer",
                            lambda **k: self._Srv(results))
        checks: list = []
        updater._append_live_probe(checks)
        return {c[0]: c for c in checks}

    def test_a_working_search_and_fetch_read_as_facts(self, monkeypatch):
        rows = self._run(monkeypatch, {
            "web_search": {"results": [{"title": "t"}] * 3,
                             "engines_used": ["baidu", "bing", "sogou"],
                             "rerank_mode": "neural"},
            "web_read": {"status": 200, "total_extracted_chars": 225,
                            "fetcher_used": "http"},
        })
        assert rows["live search"][1] == "ok", rows["live search"]
        assert "3 result(s)" in rows["live search"][2]
        assert rows["live fetch"][1] == "ok" and "200" in rows["live fetch"][2]

    def test_an_empty_engine_pool_is_not_reported_as_a_broken_install(self, monkeypatch):
        """免密引擎没有 SLA：全冷却是网络事实，下一句该是 engines probe 而不是重装。"""
        rows = self._run(monkeypatch, {
            "web_search": {"results": [], "engines_used": []},
            "web_read": {"error": "connection refused"},
        })
        assert rows["live search"][1] == "info", rows["live search"]
        assert "engines probe" in rows["live search"][3]
        assert rows["live fetch"][1] == "info", rows["live fetch"]
        assert "pip install" not in rows["live fetch"][3]


class TestItem11StateInventory:

    def _rows(self, monkeypatch, home):
        monkeypatch.setenv("DHOLE_HOME", str(home))
        checks: list = []
        updater._append_state_inventory(checks)
        return {c[0]: c for c in checks}

    def test_the_inventory_counts_what_is_there(self, tmp_path, monkeypatch):
        home = tmp_path / "h"
        home.mkdir()
        (home / "cache.db").write_bytes(b"x" * 1024)
        rows = self._rows(monkeypatch, home)
        assert "1 file(s) + 0 dir(s)" in rows["state dir contents"][2], rows

    def test_the_cookie_jar_is_named_before_anyone_pastes_the_output(self, tmp_path,
                                                                     monkeypatch):
        home = tmp_path / "h"
        home.mkdir()
        (home / "sessions.db").write_bytes(b"x")
        rows = self._rows(monkeypatch, home)
        assert "credentials on disk" in rows, rows
        assert "cookie VALUES" in rows["credentials on disk"][2]

    def test_a_home_without_credentials_does_not_claim_any(self, tmp_path, monkeypatch):
        home = tmp_path / "h"
        home.mkdir()
        rows = self._rows(monkeypatch, home)
        assert "credentials on disk" not in rows, rows


class TestItem12ConnectCost:

    def test_the_row_matches_the_measured_payload(self, monkeypatch):
        """这一行印的是每次连接要付的固定成本，它必须与 tests/tool_payload_measure.py
        量的是同一个数 —— 否则诊断命令自己就成了一个过期数字的来源。

        口径写的是 `ACTIVE_INSTRUCTIONS`（这个进程真正发出去的那份），不是全量字面量：
        默认注册面下两者同值，所以这条判据对未收窄的进程与上一条一样，收窄过的进程
        由下面那条子进程用例负责。
        """
        import json

        import dhole_mcp.server as srv_mod

        checks: list = []
        updater._append_wire_cost(checks)
        row = {c[0]: c for c in checks}["connect cost"]
        defs = srv_mod.MasterFetchServer._enabled_tool_defs()
        minimal = [{"name": td["name"], "description": td["description"],
                    "inputSchema": td["inputSchema"],
                    **({"annotations": td["annotations"]}
                       if "annotations" in td else {})} for td in defs]
        expect = (len(json.dumps({"tools": minimal}, separators=(",", ":"),
                                 ensure_ascii=False))
                  + len(srv_mod.ACTIVE_INSTRUCTIONS))
        assert f"{expect} chars" in row[2], row
        assert f"{len(defs)} tool(s)" in row[2], row

    def test_a_narrowed_registration_prices_only_what_that_process_serves(self):
        """DHOLE_TOOLS 收窄过的进程，doctor 报的必须是它自己付的那一笔。

        真起一台解释器（这旋钮 import 期冻结，进程内改不了），只问 connect cost 那一行。
        全量 instructions 是 2,475，`web_read,web_search` 的 wire 实发 1,501
        （tests/test_the_dhole_tools_price_is_the_wire_itself.py 用真 stdio 量过 12,020
        的总数；11,382 → 11,480 是体验修复刀3-C5 给 `web_read` 的 `timeout` 补上
        "一次尝试封顶 30s"那一句，11,480 → 11,866 是刀3-W1 的 `headers` —— 这一组里
        只有读的那扇门付它；11,866 → 11,894 是刀3-D2 的 `confirm`/`cdp_url` 两句 +28，
        两颗都长在 `web_read` 身上；11,894 → 11,887 是期4 第六刀 −7：`web_search` 那颗
        `before=` 的拒辞换成逐行判决的说法，新句比旧句短 7 字；11,887 → 12,020 是 P0
        纵向搜索给 `web_search` 的 `sources` / `sort` 两颗参数 +133）。按全量计价会报一个谁都没付的数，而且它恰好把"想少
        付就用 DHOLE_TOOLS"这句建议变成了假话。
        """
        import os
        import subprocess
        import sys

        code = (
            "import dhole_mcp.server as s, dhole_mcp.updater as u\n"
            "checks = []\n"
            "u._append_wire_cost(checks)\n"
            "print([c[2] for c in checks if c[0] == 'connect cost'][0])\n"
            "print('ACTIVE', len(s.ACTIVE_INSTRUCTIONS), 'FULL', len(s.DHOLE_INSTRUCTIONS))\n"
        )
        env = {k: v for k, v in os.environ.items() if k != "DHOLE_TOOLS"}
        env["DHOLE_TOOLS"] = "web_read,web_search"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, timeout=180)
        assert out.returncode == 0, out.stderr[-600:]
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        row, face = lines[-2], lines[-1]
        # 12,020 → 12,866：R3 `include_headers`（+353，`web_read` 付）与 R4 `save_to`
        # + `max_save_bytes`（+455，同一颗工具的同一句账）。两次都只有 `web_read`
        # 长，`web_search` 一字未动 —— 另两颗不在这一组里。
        assert "12866 chars" in row, f"收窄后的 doctor 报的不是这台进程付的账：{row}"
        assert "2 tool(s)" in row, row
        assert "ACTIVE 1501" in face and "FULL 2727" in face, (
            f"这道判据依赖两份 instructions 不同值：{face}")

    def test_an_unreadable_registry_degrades_to_a_note(self, monkeypatch):
        import dhole_mcp.server as srv_mod

        def boom(*a, **k):
            raise RuntimeError("no registry")

        monkeypatch.setattr(srv_mod.MasterFetchServer, "_enabled_tool_defs",
                            classmethod(boom))
        checks: list = []
        updater._append_wire_cost(checks)
        row = {c[0]: c for c in checks}["connect cost"]
        assert row[1] == "info", row


@pytest.mark.live
def test_the_live_path_runs_end_to_end_on_a_real_machine(isolated_home, capsys):
    """`-m live` 才跑：真 spawn、真搜索、真抓取，证明上面那些打桩不是自说自话。"""
    from dhole_mcp import updater as up
    up.LIVE_CHECKS = True
    assert up.doctor() in (0, 1)
    out = capsys.readouterr().out
    assert "skipped by the test harness" not in out, out
    assert "live search" in out and "live fetch" in out, out


# ============================================================================
# 来源：test_a_header_you_named_is_a_header_the_site_receives.py
# ============================================================================
# W1：调用方点名的头必须真的到线上，而到不了线上的名字必须当场具名退回。

URL = "https://example.com/"
PAGE = (b"<html><head><title>T</title></head><body><p>"
        + b"Real body text so the extractor has something to chew on. " * 4
        + b"</p></body></html>")

# conftest 的 `_offline_dns`（autouse）把 getaddrinfo 钉成公网地址。靶站是真 socket，
# 真解析器要在**导入时**抓住，否则夹具里拿到的是被钉住的那个（先例见
# test_a_private_sitemap_is_read_when_the_door_is_open.py 的同一句话）。
_REAL_GETADDRINFO = socket.getaddrinfo


class _Recorder:
    """一个把每一发的头记下来的最小站点。"""

    def __init__(self):
        self.received: list[tuple[str, dict]] = []

    def handler(self):
        rec = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                rec.received.append((self.path,
                                     {k.lower(): v for k, v in self.headers.items()}))
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)

            def do_GET(self):
                self._answer()

            def do_POST(self):
                self._answer()
        return Handler

    def page_headers(self) -> dict:
        """落在页面那一发的头（robots.txt / 预检各发自己的一份，不算这一发）。"""
        hits = [h for p, h in self.received if p.split("?")[0] == "/"]
        assert hits, f"页面从未被请求：{[p for p, _ in self.received]}"
        return hits[-1]

    def paths(self) -> list[str]:
        return [p for p, _ in self.received]


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    rec = _Recorder()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), rec.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # 只走**存下来的**那一份放行。conftest 把 identity.db 重定向进每用例自己的
    # tmp_path，所以它出不了这一格；而直接推 `_ALLOW_PRIVATE` 那颗 ContextVar 会
    # 漏出用例之外（实测：同一套件里后面 20 条"私网必须被拒"的守卫全绿变全红 ——
    # 门在进程级别被我自己撑开了）。工具每次调用都从存储重推作用域，所以存储这一份
    # 就够了，多的那一推只有副作用。
    identity.set_value("allow_private", True)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/", rec
    finally:
        srv.shutdown()
        srv.server_close()


def _call(tool: str, args: dict):
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _tier_seen(monkeypatch, tool="web_read", **args):
    """抓 HTTP 层那一发实际收到的参数（`get` 是两条整篇读路的汇点）。"""
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return server_mod.ResponseModel(url=url, status=200, content=["body text"],
                                         fetcher_used="http", content_ok=True,
                                         content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    args.setdefault("url", URL)
    _call(tool, args)
    return seen


def _stub_tiers(monkeypatch):
    """两层都替掉，按先后记下每发实际收到的参数（浏览器层不许在这条路上起来）。"""
    calls: list[dict] = []

    async def fake_get(self, url, **kwargs):
        calls.append(dict(kwargs, tier="http", url=url))
        return server_mod.ResponseModel(url=url, status=200, content=["body text"],
                                        fetcher_used="http", content_ok=True,
                                        content_type="text/html")

    async def fake_browser(self, url, **kwargs):
        calls.append(dict(kwargs, tier="stealthy", url=url))
        return server_mod.ResponseModel(url=url, status=200, content=["body text"],
                                        fetcher_used="stealthy", content_ok=True,
                                        content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake_get)
    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", fake_browser)
    # 浏览器层的入口先要一台已起的会话；真浏览器在默认那条路上被 conftest 拦着，
    # 而这一刀要读的是"浏览器层收到了什么参数"，不是浏览器本身（先例见
    # test_anti_detect_b5.py 的 `_tier_kwargs`，同一句理由）。
    async def fake_session(self, **kw):
        return "auto-under-test"

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", fake_session)
    return calls


# ─────────────── 抵达：真线上看到的就是点名的那一颗 ───────────────

def test_a_named_header_arrives_at_the_site_verbatim(site):
    """点名的头不是"记在回执里"，是站点收到的那一个值。"""
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True,
                       "headers": {"x-dhole-probe": "as-written",
                                   "accept-language": "de-DE,de;q=0.9"}})
    got = rec.page_headers()
    assert got["x-dhole-probe"] == "as-written", got
    assert got["accept-language"] == "de-DE,de;q=0.9", got


def test_a_named_header_wins_over_dholes_own_synthesized_value(site):
    """那枚实测发错的 `sec-fetch-site: "?1"`：点名它的人拿到的是他写的值。

    这一条是"覆盖得掉吗"那个问题的答案，也是把计划里那句错话（"primp 编译层覆盖
    不掉"）留在原地的东西 —— 它现在按实测写着：字典在场时 primp 让路。
    """
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True,
                       "headers": {"sec-fetch-site": "same-origin"}})
    assert rec.page_headers()["sec-fetch-site"] == "same-origin", rec.page_headers()


def test_the_template_still_rides_when_you_only_add_to_it(site, monkeypatch):
    """`headers=` 是**加**，不是**换**：默认那一档的模板仍在你那份之下垫着。

    这句话有两个读者。一个是以为"点了头就等于我控制这一发"的人 —— 公网目标照样收到
    dhole 自己的模板，它是过墙的承重件，删它会开始失败；另一个是以为 `off` 档
    什么都没关的人 —— 下面两条测的就是它。

    靶站是回环，而回环从验收刀9-1 起**不再**收到那枚编造的来路（那一句由
    `test_a_private_target_is_sent_your_headers_only.py` 测）。所以这一条测"加不是换"
    的**公网**那一半用替身层量（模板在不在，看交给 HTTP 层的 `stealthy_headers`），
    真 socket 这一半只测点名的那几颗到不到 —— 那正是这条命令原本要回答的问题。
    """
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True, "headers": {"x-dhole-probe": "1"}})
    assert rec.page_headers()["x-dhole-probe"] == "1", rec.page_headers()

    calls = _stub_tiers(monkeypatch)
    out = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "headers": {"x-dhole-probe": "1"}})
    assert calls[0]["stealthy_headers"] is True, calls[0]
    assert calls[0]["headers"] == {"x-dhole-probe": "1"}, calls[0]
    assert "wins key by key over dhole" in (out.get("summary") or ""), out.get("summary")

    url2, rec2 = site
    _call("web_read", {"url": url2, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True})
    synthesized = rec2.page_headers()
    assert "referer" not in synthesized, (
        f"回环目标还收到编造的来路（验收刀9-1 之后不该有）: {synthesized}")


def test_the_off_rung_sends_no_dhole_synthesized_header(site):
    """`anti_detect='off'` 在 HTTP 层第一次真的关掉合成（此前它是两颗字面量）。

    断的是可复现的性质而不是某一天的名单：`referer` 缺席、点名的那一颗到了、
    `sec-fetch-site` 落在真 Chrome 会用的那几个值里（同一份 16 发的抽样里 `auto` 档
    有 13 发带着 `"?1"` —— 那是 `Sec-Fetch-User` 的值）。
    """
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True, "anti_detect": "off",
                       "headers": {"x-dhole-probe": "alone"}})
    got = rec.page_headers()
    assert got["x-dhole-probe"] == "alone", got
    assert "referer" not in got, f"off 档还发编造的来路: {got}"
    assert got.get("sec-fetch-site") in (None, "none", "same-origin", "same-site",
                                         "cross-site"), \
        f"off 档带着 dhole 模板那个错值: {got.get('sec-fetch-site')!r}"


def test_the_off_rung_note_reaches_the_auto_path_too(monkeypatch):
    """自动升级那条路（不针层）也说得出这一发没带合成头。

    这条不是重复上一条：那一刀替下来的两颗字面量住在**两个**调用点上（针层那一条
    与自动升级那一条）。只测其中一个时，另一个摘回 `stealthy_headers=True` 整套
    守卫一条都不红 —— 本刀的 E2 对照实测就是这么一条 GREEN 自己把自己指出来的。
    """
    calls = _stub_tiers(monkeypatch)
    out = _call("web_read", {"url": URL, "cache_ttl": 0, "anti_detect": "off"})
    assert [c["tier"] for c in calls][:1] == ["http"], calls
    assert calls[0]["stealthy_headers"] is False, calls[0]
    assert "no dhole-synthesized headers" in (out.get("summary") or ""), out.get("summary")


def test_the_default_rung_does_not_claim_a_switch_that_never_flipped(monkeypatch):
    """反向：`auto`（默认）那一发不许带这句话 —— 说了就是给一次没发生的改动做证。"""
    calls = _stub_tiers(monkeypatch)
    out = _call("web_read", {"url": URL, "cache_ttl": 0})
    assert calls[0]["stealthy_headers"] is True, calls[0]
    assert "no dhole-synthesized headers" not in (out.get("summary") or ""), out


@pytest.mark.live
def test_the_auto_path_sends_no_invented_referrer_when_the_rung_is_off(site):
    """真 socket 上的同一条对照：不针层、只点档位，站点就收不到那枚编造的来路。"""
    url, rec = site
    _call("web_read", {"url": url, "cache_ttl": 0, "ignore_robots": True,
                       "anti_detect": "off", "headers": {"x-dhole-probe": "auto"}})
    got = rec.page_headers()
    assert "referer" not in got, f"自动升级那条路还在发合成头: {got}"
    assert got["x-dhole-probe"] == "auto", got


def test_the_default_rung_still_synthesizes_the_template():
    """零回归的那一半：`auto`（默认）那一发仍然把 dhole 自己的模板垫在你那份之下。

    编造的 google `referer` 是过墙的承重件，它在公网上**留着** —— 这一条不是为它辩护，
    是把"默认没动"钉住，防止下一次有人把它当漏掉的分支删了。

    量的是 `_build_headers()` 这一层（先例：`test_a_machine_without_browserforge_builds_
    what_it_always_built` 也直接读它）。真 socket 那半边量不了了：靶站是回环，而回环从
    验收刀9-1 起由 dhole 自己把模板摘掉（那一句在
    `test_a_private_target_gets_no_invented_referrer.py` 里测）。这里也不许断言"线上没有
    user-agent"：primp 自己那份 Chrome 画像照样在发，摘掉的只是 dhole 合成的那一份。
    """
    from dhole_mcp.fetcher import HTTPSession

    assert HTTPSession(stealthy_headers=True)._build_headers()["referer"] == \
        "https://www.google.com/"
    merged = HTTPSession(stealthy_headers=True)._build_headers({"x-dhole-probe": "1"})
    assert merged["x-dhole-probe"] == "1", merged
    assert merged["referer"] == "https://www.google.com/", merged
    # 模板被摘掉的那一发（`off` 档与回环共用这一位）只剩你点名的那几颗。
    assert HTTPSession(stealthy_headers=False)._build_headers({"x-dhole-probe": "1"}) \
        == {"x-dhole-probe": "1"}


def test_the_browser_tier_is_given_the_named_headers(monkeypatch):
    """浏览器层拿到的是同一份字典（`extra_headers` 本来就是它的参数）。"""
    calls = _stub_tiers(monkeypatch)
    _call("web_read", {"url": URL, "force_fetcher": "stealthy", "cache_ttl": 0,
                       "headers": {"x-dhole-probe": "browser-tier"}})
    assert [c["tier"] for c in calls] == ["stealthy"], calls
    assert calls[0]["extra_headers"] == {"x-dhole-probe": "browser-tier"}, calls[0]


@pytest.mark.live
def test_a_real_browser_sends_the_named_header(site):
    """真浏览器那一发确实带着它（量过一次才敢替这一层说话）。"""
    url, rec = site
    out = _call("web_read", {"url": url, "force_fetcher": "stealthy", "cache_ttl": 0,
                             "ignore_robots": True,
                             "headers": {"x-dhole-probe": "browser-tier",
                                         "accept-language": "nl-NL"}})
    assert out["status"] == 200, out.get("error")
    got = rec.page_headers()
    assert got["x-dhole-probe"] == "browser-tier", got
    assert got["accept-language"] == "nl-NL", got


# ─────────────── 具名拒绝：三族名字，各自那扇门的地址 ───────────────

@pytest.mark.parametrize("name,door", [
    ("cookie", "cred_defaults"),
    ("Cookie", "cred_defaults"),
    (" authorization ", "cred_defaults"),
    ("authorization", "cred_defaults"),
    ("Proxy-Authorization", "cred_defaults"),
    ("user-agent", "TLS"),
    ("If-None-Match", "extraction_type='check'"),
    ("if-modified-since", "extraction_type='check'"),
])
def test_a_name_that_belongs_elsewhere_is_refused_with_its_door(site, name, door):
    """拒绝必须点名是哪一颗头、为什么、去哪 —— 且一发都不许发出去。"""
    url, rec = site
    out = _call("web_read", {"url": url, "ignore_robots": True, "headers": {name: "x"}})
    blob = json.dumps(out, ensure_ascii=False)
    assert "invalid_request" in blob, blob[:300]
    assert name.strip().lower() in blob, f"没点名被拒的那一颗: {blob[:300]}"
    assert door in blob, f"没把那条路的地址说出去（要找 {door!r}）: {blob[:400]}"
    assert rec.received == [], f"被拒的调用发过请求: {rec.paths()}"


def test_the_shape_gate_is_the_one_every_header_path_already_used(site):
    """形状走的是既有那道 `validate_headers`：值必须是字符串、不许换行、不许数到 51。"""
    url, rec = site
    for bad, needle in (({"x-a": 5}, "string"),
                        ({"x-a": "one\r\ntwo"}, "newline"),
                        ({f"k{i}": "v" for i in range(51)}, "50"),
                        ({"connection": "close"}, "internally")):
        out = _call("web_read", {"url": url, "ignore_robots": True, "headers": bad})
        blob = json.dumps(out, ensure_ascii=False)
        assert "headers=" in blob and needle in blob, (bad, blob[:260])
    assert rec.received == [], rec.paths()


def test_a_serialized_object_is_accepted_and_a_list_is_named_by_name(monkeypatch):
    """序列化客户端把整段 JSON 发过来是常态（既有的 dict 参数都这样收）；
    数组不是头字典 —— 那句话要说得出是哪一颗参数。"""
    seen = _tier_seen(monkeypatch, force_fetcher="http", headers='{"x-a": "1"}')
    assert seen["headers"] == {"x-a": "1"}, seen
    out = _call("web_read", {"url": URL, "headers": ["x-a"]})
    blob = json.dumps(out, ensure_ascii=False)
    assert "headers" in blob and "object" in blob, blob[:300]


# ─────────────── 不许静默：落点说得出这次骑在哪一层 ───────────────

def test_the_summary_says_your_keys_win_over_the_template(monkeypatch):
    """`headers=` 不替换 dhole 的模板，只压过它 —— 这句话不许只在源码里。

    措辞说的是**机制**而不是"这一发"：同一条路也走缓存命中，而那一发没有包可发，
    一句"你的头骑在模板之上"就成了一句关于没发生过的事的断言。
    """
    seen = _tier_seen(monkeypatch, force_fetcher="http",
                      headers={"x-a": "1"})
    assert seen["headers"] == {"x-a": "1"}, seen
    out = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "headers": {"x-a": "1"}})
    summary = out.get("summary") or ""
    assert re.search(r"wins key by key over dhole.s own browser template", summary), summary
    assert "anti_detect='off'" in summary, "没说出那把真正的开关: " + summary


def test_an_empty_dict_is_not_silently_the_same_as_no_parameter(monkeypatch):
    """`headers={}` 读起来像"这次不发多余的"，实际什么都没改 —— 那就说出来。"""
    _tier_seen(monkeypatch, force_fetcher="http", headers={})
    out = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "headers": {}})
    assert "names no header" in (out.get("summary") or ""), out.get("summary")


def test_the_rung_note_lands_where_the_request_actually_ran(monkeypatch):
    """`off` 的说法住在跑过 HTTP 层的那条路上，不住在参数解析那一层。

    反过来的写法会把这句话发到根本没过 HTTP 的调用上（`force_fetcher='stealthy'`），
    而读者会去找一台没发出去的请求。
    """
    calls = _stub_tiers(monkeypatch)
    off = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "anti_detect": "off"})
    assert [c["tier"] for c in calls] == ["http"], calls
    assert "no dhole-synthesized headers" in (off.get("summary") or ""), off.get("summary")
    calls.clear()
    browser_only = _call("web_read", {"url": URL, "force_fetcher": "stealthy",
                                      "cache_ttl": 0, "anti_detect": "off"})
    assert [c["tier"] for c in calls] == ["stealthy"], calls
    assert "no dhole-synthesized headers" not in (browser_only.get("summary") or ""), \
        browser_only.get("summary")


# ─────────────── 缓存与批量：一颗会改变答案的参数不该被回放 ───────────────

def test_headers_change_what_comes_back_so_they_change_the_key(site):
    """`accept-language` 换的是**正文**。指纹里没有它，缓存就把别人的答复回放给你。"""
    url, rec = site
    args = {"cache_ttl": 600, "ignore_robots": True}
    _call("web_read", {"url": url + "?ck=1", "headers": {"x-dhole-probe": "one"}, **args})
    rec.received.clear()
    _call("web_read", {"url": url + "?ck=1", "headers": {"x-dhole-probe": "two"}, **args})
    assert rec.page_headers()["x-dhole-probe"] == "two", \
        f"换了点名的头却回放了上一份: {rec.paths()}"
    rec.received.clear()
    _call("web_read", {"url": url + "?ck=1", "headers": {"x-dhole-probe": "two"}, **args})
    assert rec.received == [], (
        f"同一份头、同一个 ttl 却没有命中缓存（{rec.paths()}）—— 这条对照不成立时，"
        "上面那条『不回放』是空的：谁都不命中也叫通过")


def test_a_batch_carries_the_named_headers_to_every_row(monkeypatch):
    """`urls=[...]` 是第二条路。先例：`test_the_rung_survives_the_bulk_fanout`。"""
    rows: list = []

    async def fake(self, url, **kwargs):
        rows.append(kwargs)
        return server_mod.ResponseModel(url=url, status=200, content=["body"],
                                        fetcher_used="http", content_ok=True,
                                        content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    _call("web_read", {"urls": ["https://a.example.com/", "https://b.example.com/"],
                       "force_fetcher": "http", "cache_ttl": 0,
                       "headers": {"accept-language": "ja"}})
    assert len(rows) == 2, rows
    assert [r["headers"] for r in rows] == [{"accept-language": "ja"}] * 2, rows


def test_web_request_sends_the_named_headers_on_the_write():
    """`web_request` 从前连 `extra_headers` 都不下传 —— 签名里七颗键没有这一颗。"""
    seen: dict = {}

    async def fake(self, **kwargs):
        seen.update(kwargs)
        return server_mod.ResponseModel(url=kwargs.get("url", ""), status=200,
                                        content=[])

    original = MasterFetchServer.web_read
    MasterFetchServer.web_read = fake
    try:
        _call("web_request", {"url": "https://httpbin.org/post", "method": "POST",
                              "body": {"a": 1}, "confirm": True,
                              "headers": {"x-dhole-probe": "post"}})
    finally:
        MasterFetchServer.web_read = original
    assert seen.get("headers") == {"x-dhole-probe": "post"}, seen


# ─────────────── 边界没有跟着门一起搬：§3-17 那条还在 ───────────────

def test_the_identity_spellings_are_still_refused_as_top_level_names():
    """新门开的是"你说的话"，不是"你是谁"：旧拼写照样以名字被拒。"""
    for key in ("auth", "cookies", "proxy", "extra_headers", "allow_private"):
        with pytest.raises(ValueError) as exc:
            asyncio.run(MasterFetchServer()._dispatch("web_read", {"url": URL, key: "x"}))
        msg = str(exc.value)
        assert "Unsupported argument" in msg and key in msg, msg
        assert key not in server_mod._TOP_LEVEL_ARGS["web_read"]
    assert "headers" in server_mod._TOP_LEVEL_ARGS["web_read"]


def test_a_credential_name_inside_headers_is_not_a_second_door_back():
    """`headers={"authorization": …}` 不是 `auth=` 换个马甲：拒的句子里要有那扇门。"""
    out = _call("web_read", {"url": URL, "headers": {"Authorization": "Bearer t"}})
    blob = json.dumps(out, ensure_ascii=False)
    assert "cred_defaults" in blob, blob[:300]
    assert "host" in blob, f"没说清那扇门是按主机存的: {blob[:300]}"
    assert "invalid_request" in blob, blob[:200]


# ─────────────── wire 面与手册：只读一处也路由得动 ───────────────

def _props(tool):
    td = next(d for d in MasterFetchServer._TOOL_DEFS if d["name"] == tool)
    return td["inputSchema"]["properties"]


def test_both_doors_advertise_the_parameter_and_one_author_names_the_doors():
    """web_read 说全三族与各自的门；web_request 指着那一处作者（同 anti_detect 的
    短指针那条例外：一份阶梯两处够得着，第二份是纯重复的字符税）。"""
    read = _props("web_read")["headers"]["description"]
    assert len(read) <= 296, len(read)
    for needle in ("cookie", "authorization", "proxy-authorization", "user-agent",
                   "if-*", "cred_defaults", "extraction_type='check'",
                   "anti_detect='off'", "win"):
        assert needle in read, (needle, read)
    write = _props("web_request")["headers"]["description"]
    assert len(write) <= 296, len(write)
    assert "web_read.headers" in write, write
    assert "refused" in write and "key by key" in write, write


def test_the_manual_names_the_caps_and_the_measured_asymmetry():
    from dhole_mcp import guide
    _header, behavior = guide.read("behavior")
    for needle in ("`headers=`", "50 names", "8KB", "sec-fetch-site"):
        assert needle in behavior, needle
    assert "Adding is not replacing" in behavior, "没说清这是加，不是换"
    _h, trouble = guide.read("troubleshooting")
    # "not one coherent machine" 是 W1 那句话的形状；W3 把这条限制量掉了一半（手机 UA
    # 配桌面平台那半边），文字改成"曾经是三台机器 + 现在留下的那一位"，针跟着走。
    # 留着的仍然是同一条主张：这套头不是**你这台**浏览器的头。
    for needle in ("sec-purpose", "upgrade-insecure-requests",
                   "referer", "not one coherent machine"):
        assert needle in trouble, needle
