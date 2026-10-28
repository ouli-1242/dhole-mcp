"""调用日志有上限：滚到 ``.1``，但绝不碰用户自选的路径。

这条日志默认关闭、开启后纯追加，此前没有任何东西阻止它长到吃掉磁盘。
两件事必须同时成立：dhole 自己那份要滚，用户点名的那个文件不许动。
"""


from __future__ import annotations


import json

import pytest

from dhole_mcp import audit

pytestmark = pytest.mark.smoke


@pytest.fixture
def owned_log(tmp_path, monkeypatch):
    """把 ``DHOLE_USAGE_LOG=1`` 那条路径指到临时目录，并把上限压到 200 字节。"""
    target = tmp_path / "usage.jsonl"
    monkeypatch.setattr(audit.paths, "file", lambda name: target)
    monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
    monkeypatch.setattr(audit, "USAGE_LOG_MAX_BYTES", 200)
    return target


def test_the_owned_log_rolls_to_exactly_one_generation(owned_log):
    for _ in range(20):
        audit._log_tool_call("web_read", True, 1.0)

    rolled = owned_log.with_name("usage.jsonl.1")
    assert rolled.exists(), "超过上限没有滚动，日志会一直长下去"
    assert owned_log.stat().st_size < audit.USAGE_LOG_MAX_BYTES

    # 滚出去的那一代是完整的 JSONL，不是被截断的半行
    rows = [json.loads(line) for line in rolled.read_text(encoding="utf-8").splitlines() if line]
    assert rows and rows[0]["tool"] == "web_read"


def test_only_one_generation_is_kept(owned_log):
    """第二次滚动覆盖 ``.1``，不会堆出 ``.2``/``.3``。"""
    for _ in range(40):
        audit._log_tool_call("web_read", True, 1.0)

    siblings = sorted(p.name for p in owned_log.parent.iterdir())
    assert siblings == ["usage.jsonl", "usage.jsonl.1"]


def test_a_path_the_user_named_is_never_rotated(tmp_path, monkeypatch):
    """``DHOLE_USAGE_LOG=<路径>`` 是用户自己的文件，dhole 只追加。"""
    target = tmp_path / "mine.jsonl"
    target.write_text("x" * 500, encoding="utf-8")
    monkeypatch.setenv("DHOLE_USAGE_LOG", str(target))
    monkeypatch.setattr(audit, "USAGE_LOG_MAX_BYTES", 200)

    audit._log_tool_call("web_read", True, 1.0)

    assert not (tmp_path / "mine.jsonl.1").exists(), "用户自选的文件被滚动走了"
    assert target.stat().st_size > 500


def test_a_rotation_that_cannot_happen_still_writes_the_line(owned_log, monkeypatch):
    """滚动失败（文件被别的进程占着）时，这一行照样落盘。"""

    def _boom(*_args, **_kwargs):
        raise OSError("held open by another process")

    monkeypatch.setattr(audit.os, "replace", _boom)
    audit._log_tool_call("web_read", True, 1.0)

    rows = [json.loads(line) for line in owned_log.read_text(encoding="utf-8").splitlines() if line]
    assert rows[-1]["tool"] == "web_read"
