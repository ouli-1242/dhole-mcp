"""浏览器跨重启保持同一份 profile：临时目录做不到这件事。

临时 user-data-dir 意味着每次进程重启都从空 cookie 开始 —— "今天登录、明天
再抓同一个站点"永远不成立。具名 profile 落在 ``~/.dhole/profiles/<name>/``，
退出不删。代价是同一份 profile 只能有一个写者，所以这里同时钉住单写者锁。
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from dhole_mcp import browser, paths
from dhole_mcp.server import DEFAULT_PROFILE_NAME, _auto_profile_name

pytestmark = pytest.mark.smoke


def _fresh_profile() -> str:
    """这一轮专属的 profile 名（用完删）。

    不拿 `default` 当夹具：真实机器上那一格可能正被宿主里活着的 dhole 进程占着——
    那恰恰是锁在正常工作，却会让「持久档是持久的吗」这个断言取决于此刻有没有别的
    dhole 在跑。夹具自己造环境，不去读别人的。
    """
    return f"pytest-{uuid.uuid4().hex[:10]}"


def test_a_named_profile_is_persistent_and_lives_under_the_state_dir():
    name = _fresh_profile()
    directory, persistent, lock = browser.resolve_user_data_dir(name)
    try:
        assert persistent is True, "具名档必须是持久的，否则跨天还是空 cookie"
        assert lock is not None
        assert directory == str(paths.profiles_dir() / name)
    finally:
        if lock is not None:
            lock.release()
        shutil.rmtree(paths.profiles_dir() / name, ignore_errors=True)


def test_a_second_process_gets_a_temporary_profile_instead_of_failing():
    """同一份 profile 的单写者权：第二个进程退回临时目录，而不是起不来。"""
    name = _fresh_profile()
    first = browser.resolve_user_data_dir(name)
    try:
        directory, persistent, lock = browser.resolve_user_data_dir(name)
        assert persistent is False
        assert lock is None
        assert "dhole_browser_" in directory, "退回的必须是临时目录"
        assert directory != first[0]
        shutil.rmtree(directory, ignore_errors=True)
    finally:
        first[2].release()
        shutil.rmtree(paths.profiles_dir() / name, ignore_errors=True)


def test_the_lock_is_reusable_once_released():
    name = _fresh_profile()
    _, _, lock = browser.resolve_user_data_dir(name)
    lock.release()

    directory, persistent, again = browser.resolve_user_data_dir(name)
    try:
        assert persistent is True, "前一个进程退出后必须能重新拿到"
        assert again is not None
    finally:
        again.release()
        shutil.rmtree(paths.profiles_dir() / name, ignore_errors=True)


def test_no_profile_name_means_a_temporary_directory():
    directory, persistent, lock = browser.resolve_user_data_dir(None)
    assert persistent is False
    assert lock is None
    assert "dhole_browser_" in directory


def test_the_three_state_choice_prefers_explicit_then_stored_then_default():
    """``""`` 是"我要一次性会话"，不是"没存过" —— 写成 or 就会在这里出错。"""
    assert DEFAULT_PROFILE_NAME == "default"
    assert _auto_profile_name(None, None) == DEFAULT_PROFILE_NAME
    assert _auto_profile_name(None, "work") == "work"
    assert _auto_profile_name(None, "") == "", "存了空串必须压过默认值"
    assert _auto_profile_name("temp-ish", "work") == "temp-ish"
