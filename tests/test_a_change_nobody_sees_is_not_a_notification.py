"""watch --notify 的钉子：changed 弹一枚桌面通知——旁路，永不碰退出码。

被钉的行为（2026-10-24 提升清单 3；本机 toast 已实测可弹）：
  * 有 changed 才发；没 changed 一次子进程都不起；
  * label 优先（人是给 label 起名字的），3 行之后折叠计数——toast 两行，不是清单；
  * 三个平台各走自己的无依赖通道，命令按列表传参（无 shell），PowerShell 文本过
    单引号关（label 里的 `'` 翻倍，不是拼进命令）；
  * 没有 outlet（Linux 缺 notify-send）如实返回 None，不装假；
  * 弹窗失败**只配一行说明**——通知是旁路，失败不能把「页面变了」弄成「没变」，
    退出码的合同（0/1/2）一个数都不动。
"""

import subprocess
import sys

from dhole_mcp import watch as watch_mod


def _rows(*labels):
    return [{"label": lb, "url": f"https://a.example/{i}", "verdict": "changed"}
            for i, lb in enumerate(labels)]


class TestTheGate:

    def test_no_changed_rows_starts_no_process(self, monkeypatch):
        calls = []

        def _boom(*a, **k):
            calls.append(a)
            raise AssertionError("no changed rows must not fire anything")

        monkeypatch.setattr(subprocess, "run", _boom)
        assert watch_mod.notify_changed([]) is None
        assert calls == []

    def test_a_changed_sweep_fires_one_notification(self, monkeypatch):
        fired = []

        def _fake_fire(title, body):
            fired.append((title, body))
            return True

        monkeypatch.setattr(watch_mod, "_fire", _fake_fire)
        line = watch_mod.notify_changed(_rows("pricing"))
        assert fired == [("dhole watch", "pricing")]
        assert "desktop notification sent" in line and "pricing" in line


class TestTheMessage:

    def test_labels_win_over_urls_and_overflow_folds(self, monkeypatch):
        monkeypatch.setattr(watch_mod, "_fire", lambda t, b: True)
        rows = _rows("pricing", "status", "changelog", "blog", "docs")
        line = watch_mod.notify_changed(rows)
        # 三个 label 上 toast，剩下的折叠成计数
        assert "pricing, status, changelog and 2 more" in line

    def test_a_row_without_label_names_the_url(self, monkeypatch):
        monkeypatch.setattr(watch_mod, "_fire", lambda t, b: True)
        line = watch_mod.notify_changed([{"label": "", "url": "https://a.example/x",
                                          "verdict": "changed"}])
        assert "https://a.example/x" in line


class TestTheCommands:

    def test_windows_goes_through_powershell_with_the_own_appid(self, monkeypatch):
        cmd = []
        monkeypatch.setattr(sys, "platform", "win32")

        def _run(args, **k):
            cmd.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", _run)
        assert watch_mod._fire("dhole watch", "pricing") is True
        args = cmd[0]
        assert args[0] == "powershell" and "-NoProfile" in args
        script = args[args.index("-Command") + 1]
        # AppId 是 PowerShell 自己的：未注册的 AppId 在 Win11 上被通知中心静默丢弃
        assert "1AC14E77" in script and "ToastText02" in script
        # 文本按单引号字面量进命令，双引号不出现（列表传参无 shell）
        assert "'dhole watch'" in script and "'pricing'" in script

    def test_a_quote_in_the_text_is_doubled_not_injected(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        cmd = []

        def _run(args, **k):
            cmd.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", _run)
        watch_mod.notify_changed(_rows("it's alive"))
        script = cmd[0][cmd[0].index("-Command") + 1]
        assert "'it''s alive'" in script

    def test_macos_goes_through_osascript(self, monkeypatch):
        cmd = []
        monkeypatch.setattr(sys, "platform", "darwin")

        def _run(args, **k):
            cmd.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", _run)
        assert watch_mod._fire("dhole watch", "pricing") is True
        assert cmd[0][0] == "osascript" and "pricing" in cmd[0][-1]

    def test_linux_without_notify_send_says_so(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert watch_mod._fire("dhole watch", "pricing") is False
        line = watch_mod.notify_changed(_rows("pricing"))
        assert line and "no desktop notification outlet" in line

    def test_linux_with_notify_send_uses_it(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/notify-send"
                            if name == "notify-send" else None)
        cmd = []

        def _run(args, **k):
            cmd.append(args)
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", _run)
        assert watch_mod._fire("dhole watch", "pricing") is True
        assert cmd[0][:2] == ["notify-send", "dhole watch"]


class TestTheSideChannel:

    def test_a_failed_toap_never_touches_the_exit_code_contract(self, monkeypatch):
        """弹窗炸了只配一行说明——它不是那晚的页面判决。"""
        def _boom(*a, **k):
            raise OSError("no powershell here")

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(subprocess, "run", _boom)
        line = watch_mod.notify_changed(_rows("pricing"))
        assert line.startswith("desktop notification failed:")
        # 而退出码的作者（watch.run）根本没被通知碰过——这是本测试文件里
        # TestTheGate.test_a_changed_sweep_fires_one_notification 的反面：任何
        # notify_changed 的输出都不是 verdict，只是终端上的一行 dim。

    def test_a_sweep_with_changes_exits_1_with_or_without_the_flag(self):
        """退出码的作者与通知出口正交：checker 桩给 changed，exit_code=1 不变。"""
        import asyncio

        async def checker(url):
            return {"changed": True, "status": 200}

        async def go():
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory() as td:
                wl = str(Path(td) / "watchlist.json")
                watch_mod.add("https://a.example/p", path=wl)
                out = await watch_mod.run_locked(checker, path=wl)
                return out["exit_code"]

        assert asyncio.run(go()) == 1
