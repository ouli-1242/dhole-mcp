"""孤儿 profile 只在"没人拿着它"且"放够久"时才清。

两个条件缺一不可：只看年龄会删掉一个长跑会话的活目录；只看占用判定会在
Windows 之外的平台上删掉一切。这里把四类边界都钉住。
"""


from __future__ import annotations


import pytest

import os
import time
from pathlib import Path

from dhole_mcp import browser

pytestmark = pytest.mark.smoke


def _mkprofile(root: Path, name: str, age_s: float) -> Path:
    d = root / name
    d.mkdir()
    (d / "Preferences").write_text("{}", encoding="utf-8")
    stamp = time.time() - age_s
    os.utime(d, (stamp, stamp))
    return d


def test_an_old_unheld_profile_is_removed_and_a_fresh_one_is_not(tmp_path):
    old = _mkprofile(tmp_path, "dhole_browser_old", age_s=7 * 3600)
    fresh = _mkprofile(tmp_path, "dhole_browser_fresh", age_s=60)

    removed, kept = browser.sweep_orphan_profiles(tmp_path)

    assert removed == 1
    assert not old.exists(), "放了 7 小时又没人用的目录应当被清掉"
    assert fresh.exists(), "刚建出来的目录可能正被使用，不能动"
    assert kept >= 1


def test_a_profile_something_holds_open_is_left_alone(tmp_path, monkeypatch):
    held = _mkprofile(tmp_path, "dhole_browser_held", age_s=48 * 3600)

    def _busy(*_args, **_kwargs):
        raise OSError("sharing violation: another process holds a file in it")

    monkeypatch.setattr(browser.os, "rename", _busy)
    removed, _kept = browser.sweep_orphan_profiles(tmp_path)

    assert removed == 0, "改名失败说明有人拿着它，删了就是毁活会话"
    assert held.exists()


def test_only_the_dhole_prefix_is_considered(tmp_path):
    other = _mkprofile(tmp_path, "chrome_something_else", age_s=48 * 3600)
    kept_old = _mkprofile(tmp_path, "dhole_browser_old", age_s=48 * 3600)

    browser.sweep_orphan_profiles(tmp_path)

    assert other.exists(), "别的程序的临时目录不在职责范围内"
    assert not kept_old.exists()


def test_a_stale_marker_from_last_time_is_cleaned_without_waiting(tmp_path):
    """上次改名成功但 rmtree 没跑完的残骸，不该再等 6 小时。"""
    stale = _mkprofile(tmp_path, "dhole_browser_dead.stale", age_s=0)

    removed, _ = browser.sweep_orphan_profiles(tmp_path)

    assert removed == 1
    assert not stale.exists()


def test_the_current_process_own_profile_can_be_named_as_keep(tmp_path):
    mine = _mkprofile(tmp_path, "dhole_browser_mine", age_s=48 * 3600)

    removed, _ = browser.sweep_orphan_profiles(tmp_path, keep=(mine.name,))

    assert removed == 0
    assert mine.exists()


def test_a_missing_root_is_not_an_error(tmp_path):
    assert browser.sweep_orphan_profiles(tmp_path / "nope") == (0, 0)
