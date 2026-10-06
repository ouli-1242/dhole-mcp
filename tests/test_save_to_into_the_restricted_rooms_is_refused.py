r"""截图可以落进受限目录 —— 写侧原语对读侧黑名单一无所知。

症状（2026-10-06 实测，敏感根路径只验不写）：

    ACCEPTED -> C:\Windows\System32\config\evil.png
    ACCEPTED -> C:\Program Files\Vendor\evil.png
    ACCEPTED -> C:\Users\ouli\.ssh\drop.png
    同一守卫函数对照：_blocked_path_prefix('C:\\Windows\\System32\\config\\evil.png')
    → 'c:\\windows'（读侧会拒，写侧没接）

读路径守卫（doc_parse）与写原语（save_to）各过各的日子。save_to 是唯一的写
原语，验证本就发生在浏览器冷启动之前（函数自己的 docstring），拒绝同样发生在
启动前 —— 一个打错字的代价是即时拒绝，写进受限目录的代价却要等浏览器起来
之后才可能被发现。

判据：受限目录里的图片命名路径在 _resolve_save_to 处 ValueError（不进浏览器
层）；TEMP 的正常路径行为逐项不变（G10 的覆盖/父目录/后缀语义不动）。用户
目录用沙箱假 HOME，断言停在路径判定层。

已知残余（报告同款，如实记录）：启动文件夹（AppData\\Roaming\\...\\Startup）
不在读侧黑名单的 ＋6 项里，本刀之后仍然可写 —— 但写原语只收图片后缀，
落进启动文件夹的 .png 本身不构成持久化。
"""

from __future__ import annotations

import os

import pytest

from dhole_mcp.actions import _resolve_save_to


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    def fake_expanduser(p):
        # Path.expanduser() 以裸 '~' 调用，os.path.expanduser('~/x') 以全串调用
        if p == "~":
            return str(tmp_path)
        if p.startswith("~/"):
            return str(tmp_path / p[2:])
        return p
    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)
    return tmp_path


@pytest.mark.parametrize("target", [
    "C:\\Windows\\System32\\config\\evil.png",
    "C:\\Program Files\\Vendor\\evil.png",
    "C:\\ProgramData\\ssh\\drop.png",
])
def test_system_rooms_refuse_the_screenshot(target):
    with pytest.raises(ValueError, match="restricted"):
        _resolve_save_to(target, "png")


@pytest.mark.parametrize("target", ["~/.ssh/drop.png", "~/.dhole/drop.png"])
def test_user_credential_rooms_refuse_the_screenshot(sandbox_home, target):
    with pytest.raises(ValueError, match="restricted"):
        _resolve_save_to(target, "png")


def test_a_normal_temp_path_still_resolves(tmp_path):
    got = _resolve_save_to(str(tmp_path / "shot.png"), "png")
    assert got.name == "shot.png"


def test_the_refusal_fires_before_any_browser_exists(sandbox_home):
    """拒绝发生在验证层（浏览器冷启动前），错误文本不来自浏览器层。"""
    with pytest.raises(ValueError) as ei:
        _resolve_save_to("C:\\Windows\\System32\\config\\evil.png", "png")
    assert "browser" not in str(ei.value).lower()
