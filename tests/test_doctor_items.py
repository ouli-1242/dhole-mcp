"""B2-5 · `dhole --doctor` 的计划 12 项。

doctor 跑在"已经坏了"的时候，所以它的两条老规矩不变：绝不抛异常、退出码能gate。
这一批把检查项重排成 12 项（顺序本身是文档：从"这台机器能不能跑"读到"这次连接付了
多少 token"），这里既钉每一项都在（少一行就红），也钉每一项的失败分支 —— 故障是
注入的，而不是指望测试机恰好坏在对的地方。

第 5 项（真起一个 stdio 子进程）与第 10 项（真发一次搜索与抓取）由 conftest 的挂具
关掉；关掉时 doctor 会印"skipped by the test harness"，所以下面这些用例打桩的是
`_append_*` 本体，而不是整份报告。
"""

import json
import sys

import pytest

from dhole_mcp import updater

# 12 项各自必须出现的那一行标签。少一项 = 装机者少一个答案。
REQUIRED_ROWS = (
    "python + platform",      # 1
    "dependency layers",      # 2
    "chromium binary",        # 3
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
        它是"坏了才跑"的命令，跑完不该改变现场。"""
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
        assert str(self.REV + 71) not in line, line


class TestItem4RerankModel:

    class _Model:
        def __init__(self, digest):
            self.name = "bge-zh"
            self.approx_bytes = 279_000_000
            self.publisher_sha256 = digest

    def _patch(self, monkeypatch, tmp_path, *, downloaded, digest):
        from dhole_mcp import reranker

        d = tmp_path / "models" / "bge-zh"
        d.mkdir(parents=True)
        if downloaded:
            (d / "model.onnx").write_bytes(b"x" * 5000)
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
        量的是同一个数 —— 否则诊断命令自己就成了一个过期数字的来源。"""
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
                  + len(srv_mod.DHOLE_INSTRUCTIONS))
        assert f"{expect} chars" in row[2], row
        assert f"{len(defs)} tool(s)" in row[2], row

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
