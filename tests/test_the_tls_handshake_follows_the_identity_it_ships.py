"""头说 Chrome/147 而握手按 primp 自选的那一颗走 —— 现在握手跟着头走（R02-080 · R02-F73）。

这条是 R02-F70 的**另一半**。F70 拦掉的是"字典里缺 brand 表时由 primp 的预设说话"那一支（两颗头
互相否认）；剩下的这一半是：`impersonate="chrome"` 不带版本，primp 每次**新建客户端**自选一个
Chrome 大版本（实测同一进程三发分别 149 / 153 / 150），而 dhole 的头钉在**进程**那份身份上
（`_PROCESS_IDENTITY`，这条本身是对的）。于是 UA 说 147、握手可能是 153 那一套 —— 两个各自合法
的作者，合起来是一副没人见过的浏览器。

两趟实测（`review/.runtime/p4/r80-shape.json` 与 `r80-shape2.json`，只打回环 echo）把方案钉死了：

* 接受的名字是 `chrome` + `chrome_144…153`（99–143 与 154+ 一律 `Invalid impersonate`），
  清单只存在于 primp.pyd 的 Rust 枚举里（`.pyi` 里是 `impersonate: str | None`）
  ⇒ **源码里不许有版本表**：窗口会变，表会腐烂；被拒就退回今天那颗，是自维持的。
* **版本化的预设里躺着手机形状**：不钉 OS 时 `chrome_144` 交出 `…(iPhone; CPU iPhone OS 18_7…)
  … CriOS/144.0.0.0…`。钉住 `impersonate_os` 取 windows/macos/linux 之后 chrome 144–153 十颗
  **全部**回到桌面且 major 与名字一致。
* firefox 那一族即使钉了桌面 OS，`firefox_141…145` 仍交回手机形状；safari 只有 `_26` 被接受而
  browserforge 摇出的是 17/18（必然被拒）。⇒ 这两族**不钉**，保持今天的形状：宁可留着"版本各
  自摇"这一位已知的不一致，也不引入"头是桌面、握手是手机"这一位未知的。

所以这里的判据不是"版本号对上了"，是**三件事一起**：读得出 major 才钉；读得出桌面 OS 才钉；
钉出去之后线上那发仍必须是桌面的。
"""

from __future__ import annotations

import asyncio
import types

import pytest

from dhole_mcp import fetcher
from dhole_mcp.fetcher import HTTPSession, _impersonate_for


def _ua(major: int, os_blob: str = "Windows NT 10.0; Win64; x64") -> str:
    return (f"Mozilla/5.0 ({os_blob}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


# ───────────────── 一 · 什么时候才钉：读得出 major 且读得出桌面 OS ─────────────────

def test_a_desktop_chrome_identity_pulls_the_handshake_to_its_own_major():
    got = _impersonate_for("chrome", {"user-agent": _ua(147)}, "windows")
    assert got == "chrome_147", got


def test_edge_is_pinned_the_same_way_chrome_is():
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/140.0.0.0 Safari/537.36 Edg/150.0.0.0")
    assert _impersonate_for("edge", {"user-agent": ua}, "windows") == "edge_150"


def test_the_brand_list_is_the_second_reader_not_the_only_one():
    """UA 里读不出 Chrome/ 时读 `sec-ch-ua`；两处都读不出就不钉（不猜一个更像的）。"""
    got = _impersonate_for("chrome", {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149"'}, "macos")
    assert got == "chrome_149", got
    assert _impersonate_for("chrome", {"user-agent": "curl/8.0"}, "linux") == "chrome"


@pytest.mark.parametrize("family", ["firefox", "safari"])
def test_the_two_families_with_measured_holes_are_left_alone(family):
    """firefox_141…145 钉了桌面 OS 仍交回手机形状；safari 的版本名基本会被拒。

    这两条守的是**实测的洞**，不是保守：钉它们会把"版本不一致"换成"设备不一致"。
    """
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
          if family == "firefox" else
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
    assert _impersonate_for(family, {"user-agent": ua}, "windows") == family


def test_no_os_read_means_no_version_pin():
    """OS 读不出（或落到 "random"）时不钉：那一支就是手机形状钻进来的那条路。"""
    for os_name in ("", "random"):
        assert _impersonate_for("chrome", {"user-agent": _ua(147)}, os_name) == "chrome", os_name
    assert _impersonate_for("chrome", {}, "windows") == "chrome", "身份缺席（browserforge 没装）"


def test_an_unknown_family_string_is_passed_through_untouched():
    """`impersonate` 可以是调用方给的怪名字（`_header_family` 认不出=不钉家族）。

    这一发不许变成异常，也不许被改写成 `chrome_<major>`：那是把别人给的值重铸了一遍。
    """
    assert _impersonate_for("opera", {"user-agent": _ua(120)}, "windows") == "opera"
    assert _impersonate_for("chrome_120", {"user-agent": _ua(120)}, "windows") == "chrome_120"


# ───────────── 二 · primp 拒了版本名：退回今天那颗，且这不是失败 ─────────────

class TestTheRejectionFallsBackNotFails:
    """primp 的窗口会变（升级一版 144 就可能没了）。被拒必须**静默退回**，不能把抓取打断。"""

    def _fake_primp(self, accepted):
        """挂的是 `fetcher.primp` 这**一个模块位**：代码读的是 `primp.Client(...)`。"""
        class _C:
            def __init__(self, **kwargs):
                name = kwargs.get("impersonate")
                if name not in accepted:
                    raise ValueError(f'Invalid impersonate: "{name}"')
                self.kwargs = kwargs

            def get(self, *a, **k):
                raise AssertionError("挂具不该发请求")

        return types.SimpleNamespace(Client=_C)

    def _init_with(self, monkeypatch, accepted, baseline):
        monkeypatch.setattr(fetcher, "primp", self._fake_primp(accepted))
        monkeypatch.setattr(fetcher, "_process_identity", lambda family: dict(baseline))
        s = HTTPSession(impersonate="chrome")
        asyncio.run(s._init_client())
        return s

    def test_a_rejected_version_name_still_builds_a_client(self, monkeypatch):
        s = self._init_with(monkeypatch, {"chrome"}, {"user-agent": _ua(147)})
        assert s._client is not None
        assert s._client.kwargs["impersonate"] == "chrome", s._client.kwargs

    def test_an_accepted_version_name_is_the_one_used(self, monkeypatch):
        s = self._init_with(monkeypatch, {"chrome", "chrome_147"},
                            {"user-agent": _ua(147)})
        assert s._client.kwargs["impersonate"] == "chrome_147", s._client.kwargs

    def test_the_os_that_follows_the_identity_is_still_pinned_on_the_retry(self, monkeypatch):
        """退回那一次**不许**把 OS 也退回 "random"：那是把另一维的错配放回来。"""
        s = self._init_with(monkeypatch, {"chrome"}, {"user-agent": _ua(147)})
        assert s._client.kwargs["impersonate_os"] == "windows", s._client.kwargs

    def test_a_hard_construction_failure_is_not_swallowed(self, monkeypatch):
        """只兜"这个名字不认识"，不兜一切异常 —— 别的构造期故障要照常冒出来。"""
        class _Boom:
            def __init__(self, **kwargs):
                raise RuntimeError("libprimp missing")

        monkeypatch.setattr(fetcher, "primp", types.SimpleNamespace(Client=_Boom))
        monkeypatch.setattr(fetcher, "_process_identity", lambda family: {"user-agent": _ua(147)})
        with pytest.raises(RuntimeError):
            asyncio.run(HTTPSession(impersonate="chrome")._init_client())
