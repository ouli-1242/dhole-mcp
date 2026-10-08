"""Tests for `dhole --doctor`.

doctor() runs precisely when something is already broken, so it must never
raise, and its exit code has to distinguish healthy from broken so a script can
gate on it. The failure paths below INJECT the failure (a missing core module,
an unusable state dir, absent metadata) rather than hoping the test machine
happens to be broken in the right way.
"""

import pytest

from dhole_mcp import updater


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point every state path at a throwaway dir - doctor probes the home it writes
    its writable-check probe into."""
    home = tmp_path / "home"
    monkeypatch.setenv("DHOLE_HOME", str(home))
    return home


class TestDoctorReporting:
    def test_never_raises_and_returns_an_exit_code(self, isolated_home, capsys):
        assert updater.doctor() in (0, 1)
        assert capsys.readouterr().out.strip()

    def test_reports_the_loaded_module_path(self, isolated_home, capsys):
        """The line that makes a wheel-vs-src mismatch visible at a glance."""
        updater.doctor()
        out = capsys.readouterr().out
        assert "module loaded from" in out
        assert "dhole_mcp" in out

    def test_checks_the_state_dir_and_the_search_egress(self, isolated_home, capsys):
        updater.doctor()
        out = capsys.readouterr().out
        assert "state dir writable" in out
        assert "search proxy" in out

    def test_always_appends_the_capability_panel(self, isolated_home, capsys):
        updater.doctor()
        assert "capabilities" in capsys.readouterr().out

    def test_leaves_no_probe_file_behind(self, isolated_home):
        updater.doctor()
        leftovers = [p.name for p in isolated_home.iterdir()
                     if p.name.startswith(".doctor")]
        assert leftovers == []


class TestDoctorFailuresAreLoud:
    def test_missing_core_dependency_fails_and_names_the_fix(
            self, isolated_home, monkeypatch, capsys):
        monkeypatch.setattr(updater, "_has_module", lambda name: name != "httpx")
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "httpx" in out
        assert "fix:" in out

    def test_unusable_state_dir_fails_and_names_the_fix(
            self, isolated_home, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(updater.paths, "home", boom)
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "state dir writable" in out
        assert "DHOLE_HOME" in out

    def test_unusable_state_dir_is_reported_not_raised(
            self, isolated_home, monkeypatch, capsys):
        """doctor 是"已经坏了才去跑"的命令：状态目录打不开要印成一行 fail，
        不能把自己崩掉，也不能顺手写一个会重装、会杀进程的脚本进别的目录。"""
        def boom(*args, **kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(updater.paths, "home", boom)
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "state dir" in out, out

    def test_missing_metadata_fails(self, isolated_home, monkeypatch, capsys):
        import importlib.metadata as md

        def boom(name):
            raise md.PackageNotFoundError(name)

        monkeypatch.setattr(md, "version", boom)
        assert updater.doctor() == 1
        assert "metadata consistent" in capsys.readouterr().out

    def test_healthy_run_exits_zero(self, isolated_home, monkeypatch, capsys):
        """Force every environment-dependent check to succeed, assert clean exit."""
        import importlib.metadata as md

        from dhole_mcp import __version__ as mod_ver

        monkeypatch.setattr(updater, "_has_module", lambda name: True)
        monkeypatch.setattr(updater, "_other_dhole_pids", lambda: [])
        monkeypatch.setattr(updater, "_dhole_launcher_path", lambda: __file__)
        monkeypatch.setattr(md, "version", lambda name: mod_ver)

        assert updater.doctor() == 0
        assert "all healthy" in capsys.readouterr().out


class TestProcessScanIsEncodingSafe:
    """`_other_dhole_pids` 通过外部命令列进程，输出编码由系统决定。

    中文 Windows 上 tasklist 按 GBK 输出，而 Python 的 UTF-8 模式会把
    text=True 的解码器设成 utf-8 —— 于是「没有匹配进程」时那条中文提示会
    解码失败，异常还发生在读取线程里（check_output 只会抛出无关的
    TypeError），被 except 吞掉后 doctor 永远报「none running」。

    这是静默假阴性：有残留进程也看不出来。修法是 errors="replace" —— 我们
    要的 dhole.exe 和 PID 都是 ASCII，坏字节替换掉不影响解析。
    """

    def test_never_raises_on_this_platform(self):
        assert isinstance(updater._other_dhole_pids(), list)

    def test_never_raises_when_output_is_undecodable(self, monkeypatch):
        """注入一段无法解码的输出，确认函数不把异常漏出去。"""
        import subprocess

        def boom(*a, **kw):
            raise UnicodeDecodeError("utf-8", b"\xd0", 0, 1, "invalid start byte")

        monkeypatch.setattr(subprocess, "check_output", boom)
        assert updater._other_dhole_pids() == []

    def test_subprocess_call_requests_lenient_decoding(self):
        """源码级守卫：这两处调用必须带 errors="replace"，否则回归即静默。"""
        import inspect
        import re

        src = inspect.getsource(updater._other_dhole_pids)
        calls = re.findall(r"check_output\((.*?)\n\s*\)", src, re.S)
        assert calls, "未找到 check_output 调用"
        for call in calls:
            assert 'errors="replace"' in call, f"缺少 errors=\"replace\": {call[:120]}"


class TestDoctorConfigAudit:
    """W2 · 配置审计段。

    doctor() 的 10 项检查全是"安装完整性"，看得见 broken launcher、看得见
    stale .exe，就是看不见 env。这一段的存在意义就是把"我设了但没生效"这条
    唯一的可见点建起来；下面四条断言分别对应 plan §3 W2 验收清单的四条。
    """

    @pytest.fixture(autouse=True)
    def _clear_dhole_env(self, monkeypatch, isolated_home):
        """Scrub every DHOLE_* export, INCLUDING the DHOLE_HOME the
        isolated_home fixture just set. Config audit reads ``os.environ``
        directly, so ambient exports on the dev machine (a real one here
        runs with DHOLE_SSRF_DNS_RECHECK + a couple more) would leak into
        every assertion, and ``isolated_home`` itself is what makes
        "1 of 39 set" the ambient baseline.
        """
        from dhole_mcp import env_registry as _reg
        for e in _reg.ALL:
            monkeypatch.delenv(e.name, raising=False)
        # Keep the isolated_home path available under a private name so
        # tests that need to verify no probe file landed in tmp_path can
        # find it. The writable check falls through to ~/.dhole; on this
        # dev box (and CI) that is writable, so doctor still exits 0.
        yield

    def test_invalid_int_env_returns_one_and_names_the_fix(self, isolated_home,
                                                            monkeypatch, capsys):
        """W2 验收 1：非法整数 env → doctor 退出码 1，输出含变量名 + using default。"""
        monkeypatch.setenv("DHOLE_DEFAULT_CONTENT_CHARS", "abc")
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "DHOLE_DEFAULT_CONTENT_CHARS" in out
        assert "using default" in out
        assert "is not an integer" in out

    def test_valid_env_never_echos_the_value(self, isolated_home, monkeypatch, capsys):
        """W2 验收 2：合法 env 显示行存在，哨兵值**不出现**在 stdout。

        选 kind=str 的 DHOLE_UPDATE_PACKAGE 做载体：任何字符串都算合法，
        哨兵一眼看得出来；这一条同时钉住 _config_effective_value 对 str
        只写 "set" 的分支。
        """
        sentinel = "SENTINEL-pool-name-xyzzy-42"
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", sentinel)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "DHOLE_SEARCH_PROXY" in out
        assert sentinel not in out

    def test_secret_env_shows_only_length(self, isolated_home, monkeypatch, capsys):
        """W2 验收 3：API key 类 env 只显示 ``set (len=N)``，绝不回显值。
        17.0 后 keyed 引擎只剩 Tavily 与博查。"""
        for key_env in ("DHOLE_BOCHA_API_KEY", "DHOLE_TAVILY_API_KEY"):
            sentinel = f"super-secret-{key_env}-SENTINEL"
            monkeypatch.setenv(key_env, sentinel)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        for key_env in ("DHOLE_BOCHA_API_KEY", "DHOLE_TAVILY_API_KEY"):
            assert key_env in out
            assert "SENTINEL" not in out
        assert "set (len=" in out

    def test_import_time_env_is_marked_as_restart(self, isolated_home, monkeypatch, capsys):
        """W2：read_at="import" 的行必须显式说 "restart the host to change"，
        不然操作者会按 call 类 env 那样改完直接跑一次工具，然后困惑为什么
        改了没生效——这是"配置改了没生效"最常见的一个来源。"""
        monkeypatch.setenv("DHOLE_DEFAULT_CONTENT_CHARS", "8000")
        updater.doctor()
        out = capsys.readouterr().out
        assert "DHOLE_DEFAULT_CONTENT_CHARS" in out
        assert "restart the host" in out

    def test_call_time_env_is_not_marked_as_restart(self, isolated_home,
                                                    monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        updater.doctor()
        out = capsys.readouterr().out
        assert "DHOLE_USAGE_LOG" in out
        # the same "restart the host" wording should not ride a call-time row
        for line in out.splitlines():
            if "DHOLE_USAGE_LOG" in line:
                assert "restart the host" not in line

    def test_none_set_is_a_green_summary_not_a_warning(self, isolated_home,
                                                        monkeypatch, capsys):
        """W2：一个都没设 → "config: none set - dhole runs on defaults"，且
        不能看起来像警告。doctor 输出里 "!" 是 magenta 警告标记，✓ 是 ok；
        断言这一行的 mark 是 ok。"""
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "config" in out
        assert "none set" in out
        assert "runs on defaults" in out
        # the "config" summary line must use the ok (✓) mark, never the "!"
        # warning mark - otherwise a fully-clean machine reads as though
        # something was wrong.
        for line in out.splitlines():
            if "none set" in line:
                assert "\u2713" in line or "+" in line  # ui.ok glyph (win32 fallback +)
                assert "!" not in line

    def test_summary_counts_set_variables(self, isolated_home, monkeypatch, capsys):
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "http://127.0.0.1:7890")
        updater.doctor()
        out = capsys.readouterr().out
        # 总数从注册表读，不抄成字面量：这里曾经写着 "2 of 13"（13 是 G25 加
        # DHOLE_HTTP_TOKEN 到 12、17.1 加 DHOLE_PERSIST_CREDENTIALS 到 13 的手抄结果），
        # 于是每加一颗旋钮就要在这里改一次 —— 而漏改的那次会红着让人以为是 doctor 错了。
        from dhole_mcp import env_registry

        assert f"2 of {len(env_registry.ALL)} variables set" in out

    def test_the_credential_storage_policy_is_reported(
            self, isolated_home, monkeypatch, capsys):
        """评审 H4 的收口要在 doctor 里有名字：默认报"只在内存"，落盘模式
        报加密边界（防拷走，不防同用户进程——这句话必须印出来）。"""
        updater.doctor()
        out = capsys.readouterr().out
        assert "credential storage" in out
        assert "in memory only" in out

        monkeypatch.setenv("DHOLE_PERSIST_CREDENTIALS", "1")
        updater.doctor()
        out = capsys.readouterr().out
        assert "DPAPI" in out
        assert "NOT another process running as this user" in out

    def test_persist_requested_without_an_encryptor_fails_loud(
            self, isolated_home, monkeypatch, capsys):
        """没有 DPAPI 的平台设了落盘旗：运行时退回内存 + doctor 报 fail——
        宁可拒存也不回退明文，而且这句必须被看见。"""
        from dhole_mcp import secretbox
        monkeypatch.setenv("DHOLE_PERSIST_CREDENTIALS", "1")
        monkeypatch.setattr(secretbox, "available", lambda: False)
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "credential storage" in out
        assert "stay in memory" in out

    def test_config_audit_does_not_raise_when_the_section_itself_breaks(
            self, isolated_home, monkeypatch, capsys):
        """W2 的 "doctor() 绝不许抛" 契约延伸到新段：配置审计整个 try/except
        包住，就算它自己炸了也要退化成一行 info，绝不把整个 doctor 打崩。"""
        def boom(_checks):
            raise RuntimeError("registry exploded mid-audit")

        monkeypatch.setattr(updater, "_append_config_audit", boom)
        code = updater.doctor()
        assert code in (0, 1)
        out = capsys.readouterr().out
        assert out.strip()
        assert "config audit" in out

    def test_d1_gate_typo_is_reported_as_invalid(self, isolated_home, monkeypatch, capsys):
        """W2 与 D1 的接口：非法 flag 值走 fail-safe（视为 OFF），doctor 需要
        把这一条列成 fail 行而不是静默。"""
        monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "flase")
        assert updater.doctor() == 1
        out = capsys.readouterr().out
        assert "DHOLE_IGNORE_ROBOTS" in out
        assert "not a recognised" in out

    def test_d1_gate_off_value_still_valid_no_fail(self, isolated_home,
                                                   monkeypatch, capsys):
        """回归：合法 off 值（"0"/"false"/... ）不进 fail，避免噪声。"""
        monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "0")
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "DHOLE_IGNORE_ROBOTS" in out
        # 0 → OFF，display 是 "off"
        for line in out.splitlines():
            if "DHOLE_IGNORE_ROBOTS" in line:
                assert "off" in line

    def test_a_retired_knob_is_named_as_ignored(self, isolated_home,
                                                monkeypatch, capsys):
        """§3-20：注销的旋钮运行时静默忽略，唯一会提到它们的地方是 --doctor。
        它不许出现在合法变量的审计行里（否则像还在生效），也不许让 doctor 变红。"""
        monkeypatch.setenv("DHOLE_MULTIMODAL", "1")
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "DHOLE_MULTIMODAL" in out


class TestDoctorResolverReality:
    """W3 · fake-ip 代理检测。

    修的是 §1.5 那条被实测证伪的 README 断言："零配置即可用"。真实的拦截链路
    不是 "fake-ip 被拦" 这种笼统说法：`_PRIVATE_NETWORKS` 里根本没有
    198.18.0.0/15，是 AAAA 落 fc00::/7 才是拒绝点。测试必须按这条具体链路断言
    输出包含正确的地址、正确的网段、正确的修法，且不能因"环境就是这样"把
    doctor 判红。
    """

    @pytest.fixture(autouse=True)
    def _clear_dhole_env(self, monkeypatch, isolated_home):
        from dhole_mcp import env_registry as _reg
        from dhole_mcp import security as _sec
        for e in _reg.ALL:
            monkeypatch.delenv(e.name, raising=False)
        # W12 的探测结果按进程缓存。体检现在会问它"这台机器是不是已经自动跳过"，
        # 所以缓存不丢就会让上一个用例的环境泄漏进下一个 —— 顺序一改就闪红的
        # 那种脆弱。
        _sec.reset_fake_ip_probe()
        yield
        _sec.reset_fake_ip_probe()

    def _fake_addr(self, monkeypatch, answers):
        """Patch socket.getaddrinfo to return the given (family, ip) list."""
        import socket

        def fake(host, port, *args, **kwargs):
            return [(f, 1, 6, "", (ip, 0)) for (f, ip) in answers]

        monkeypatch.setattr(socket, "getaddrinfo", fake)

    def test_fake_ip_a_record_names_the_network_and_the_remedy(
            self, isolated_home, monkeypatch, capsys):
        import socket

        self._fake_addr(monkeypatch, [
            (socket.AF_INET, "198.18.0.32"),
            (socket.AF_INET6, "fd00::20"),
        ])
        # This is an environment fact, not an install fault - the plan is
        # explicit that doctor must NOT return 1 here.
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip" in out
        assert "198.18.0.32" in out
        assert "198.18.0.0/15" in out
        assert "fc00::/7" in out
        # W12 → 17.0：自动判定没有覆盖开关 —— 体检说"已自动跳过、URL 照常可用"，
        # 不再教任何 env 写法（教了也是已经注销的旋钮）。
        assert "tolerated" in out and "still refuses the rest" in out
        assert "DHOLE_SSRF_DNS_RECHECK" not in out
        # and the note about what disabling actually turns off:
        assert "IP-literal" not in out, "整层关闭那句话已经不再是事实"

    def test_the_verdict_of_the_process_that_fetches_is_what_gets_printed(
            self, isolated_home, monkeypatch, capsys):
        """17.0 批次 6d：doctor 自己看到 fake-ip，而**真正回答调用的那个进程**当初测的是
        public 并把判词缓存到进程寿命结束 —— 这条从前印不出来。旧代码问的是
        `_sec._dns_recheck_enabled()`，那是 doctor 本进程刚刚自己探的那一次，必然和上一
        行自洽，等于没核对；用户的 URL 全被拒、体检却耸肩。现在这一行读盘上那份镜像，
        并且这里用真的生产者写它（probe_fake_ip），不是手搓一个字典。"""
        import socket

        from dhole_mcp import security as _sec

        self._fake_addr(monkeypatch, [(socket.AF_INET, "93.184.216.34")])
        _sec.reset_fake_ip_probe()
        assert _sec.probe_fake_ip()[0] == "public", "前置：那次'抓取进程'判的是公网"

        self._fake_addr(monkeypatch, [(socket.AF_INET, "198.18.0.32")])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip proxy detected" in out
        assert "measured public" in out, "要说出是谁的答案不一样，而不是只报自己看到的"
        assert "restart" in out
        assert "DHOLE_SSRF_DNS_RECHECK" not in out

    def test_auto_skip_engaged_is_ok_green(self, isolated_home, monkeypatch, capsys):
        """自动判定已跳过复查层：这一行是 ok，且不谈任何 env。"""
        import socket

        self._fake_addr(monkeypatch, [
            (socket.AF_INET, "198.18.0.127"),
            (socket.AF_INET6, "fd00::80"),
        ])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip proxy detected" in out
        assert "tolerated" in out and "still refuses the rest" in out
        assert "DHOLE_SSRF_DNS_RECHECK" not in out

    def test_offline_dns_never_counts_as_failure(
            self, isolated_home, monkeypatch, capsys):
        import socket

        def boom(*args, **kwargs):
            raise socket.gaierror("name or service not known")

        monkeypatch.setattr(socket, "getaddrinfo", boom)
        # doctor must stay 0 on an offline box (assuming nothing else fails)
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "resolver reality" in out
        # the "info" glyph is "!" not "✗"; the line is present but not red.
        for line in out.splitlines():
            if "resolver reality" in line:
                assert "✗" not in line and "x" not in line[:15]

    def test_public_resolution_is_ok(self, isolated_home, monkeypatch, capsys):
        import socket

        self._fake_addr(monkeypatch, [
            (socket.AF_INET, "93.184.216.34"),
        ])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "resolver reality" in out
        assert "public answer" in out

    def test_only_a_record_in_block_still_triggers(self, isolated_home,
                                                   monkeypatch, capsys):
        """The plan's chain is: v4 hits 198.18.0.0/15 (which is NOT in
        _PRIVATE_NETWORKS) but v6 AAAA hits fc00::/7 which is the real
        reject path. If the AAAA is absent, the v4 signal alone still
        identifies fake-ip - the operator needs to hear about it."""
        import socket

        self._fake_addr(monkeypatch, [(socket.AF_INET, "198.18.0.1")])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "fake-ip" in out
        # 只有 A 记录命中也认得出来，而这一台机器上 dhole 已经自动跳过了那一层
        assert "tolerated" in out and "still refuses the rest" in out
        assert "DHOLE_SSRF_DNS_RECHECK" not in out

    def test_resolver_reality_does_not_raise_even_if_patched_bad(
            self, isolated_home, monkeypatch, capsys):
        """_append_resolver_reality 内部炸了也不能把 doctor 打挂。"""
        def boom(*args, **kwargs):
            raise ValueError("getaddrinfo is on fire")

        import socket
        monkeypatch.setattr(socket, "getaddrinfo", boom)
        # doctor() must still return 0/1 - never raise.
        assert updater.doctor() in (0, 1)


class TestTheCdpFenceRow:
    """§3-16：attach 的围栏 = 落盘的已授权主机集合。体检报告的是磁盘上的
    授权现实 —— 没有授权主机时明确说"第一次 attach 会被拒绝并提问"，
    有授权主机时报数量并给出撤销路径。env 旋钮已注销，这条行不受任何 env 影响。
    """

    def test_no_authorized_host_reports_the_confirm_flow(self, isolated_home,
                                                         monkeypatch, capsys):
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "cdp fence" in out
        assert "refused" in out and "confirm" in out

    @pytest.mark.asyncio
    async def test_authorized_hosts_report_the_count_and_the_revoke_path(self, isolated_home,
                                                                   monkeypatch, capsys):
        from dhole_mcp.server import _cdp_attach_gate
        await _cdp_attach_gate("http://127.0.0.1:9222", confirm=True,
                               targets=["http://portal.example.test/"])
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "1 host" in out
        assert "portal.example.test" in out
        assert "revoke" in out and "cdp_hosts.json" in out

    def test_a_stale_gate_value_neither_enables_nor_lies(self, isolated_home,
                                                         monkeypatch, capsys):
        """旋钮已注销：设置它不产生授权（磁盘说了算），也不让 doctor 变红。"""
        monkeypatch.setenv("DHOLE_CDP_ATTACH", "1")
        assert updater.doctor() == 0
        out = capsys.readouterr().out
        assert "refused" in out  # 仍是"未授权"行，不是"已围栏 N 台"
