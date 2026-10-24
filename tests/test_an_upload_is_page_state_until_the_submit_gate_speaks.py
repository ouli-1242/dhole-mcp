"""upload：把本机文件装进 <input type=file>。

现场（2026-10-24，可提升清单 1）：web_act 的动作链有 click/fill/submit 十二颗，
唯独没有 upload——表单带附件、传头像这类真人的日常流程直接缺门。对标 Playwright
MCP 的 browser_file_upload 补上。

两条设计判断，各有钉子：

  * **upload 是页面状态，不是外发**。set_input_files 只改这一页的 DOM（fill 的
    同侪），字节真正离开本机发生在表单提交那一刻——那一步照旧由 submit 闸把关
    （confirm / 人按键），upload 不设第二道确认门。
  * **路径按读侧纪律走**。这是工具面里第一个「读本机」的动作：realpath 先展开
    符号链接（/tmp 里一枚指向 ~/.ssh 的链接绕不过名单），再过系统目录与凭据
    清单；大小帽在浏览器起来之前拦（set_input_files 会把字节装进浏览器进程）。

校验全部发生在 `_validate_actions`（浏览器还没起来的时刻）——拼错的路径值一次
即时拒绝，与 save_to 同一条理由。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dhole_mcp import actions as A
from dhole_mcp.actions import _validate_actions, build_page_action


# ── 桩 ──────────────────────────────────────────────────────────────────

class _FakePage:
    def __init__(self):
        self.set_calls: list = []
        self._listeners: list = []

    def locator(self, sel):
        return self

    @property
    def first(self):
        return self

    def nth(self, i):
        return self

    async def set_input_files(self, files, **kw):
        self.set_calls.append(list(files))

    async def evaluate(self, js, arg=None):
        return "file input"

    def on(self, event, handler):
        self._listeners.append((event, handler))

    def remove_listener(self, event, handler):
        with_zeros = [(e, h) for e, h in self._listeners if (e, h) != (event, handler)]
        self._listeners[:] = with_zeros


# ── 1 · 校验（浏览器没起来就该拒绝的）───────────────────────────────────

def test_upload_needs_a_files_list():
    with pytest.raises(ValueError, match="files"):
        _validate_actions([{"upload": {"selector": "input[type=file]"}}])


def test_upload_refuses_a_missing_file_by_name():
    with pytest.raises(ValueError, match="file not found"):
        _validate_actions([{"upload": {"selector": "#doc",
                                          "files": ["./no-such-file.pdf"]}}])


def test_upload_refuses_a_directory(tmp_path):
    with pytest.raises(ValueError, match="is a directory"):
        _validate_actions([{"upload": {"selector": "#doc",
                                          "files": [str(tmp_path)]}}])


def test_upload_refuses_restricted_paths_and_checks_realpath(tmp_path, monkeypatch):
    """读侧纪律：realpath 展开之后才过名单——/tmp 里一枚指向受限目录的链接
    绕不过去（Windows 上 symlink 要特权，这里钉的是「名单看见的是展开后的
    绝对路径」这根管子本身；名单自己的内容有它自己的钉子）。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("token", encoding="utf-8")
    import dhole_mcp.server as server_mod
    real_probe = {"called_with": None}

    def fake_blocked(real_path):
        real_probe["called_with"] = real_path
        return "~/.ssh"  # 名单说不许读——探针同时记下它看见的是哪条路径

    monkeypatch.setattr(server_mod, "_blocked_path_prefix", fake_blocked)
    with pytest.raises(ValueError, match="restricted system directory"):
        _validate_actions([{"upload": {"selector": "#doc",
                                       "files": [str(secret)]}}])
    assert real_probe["called_with"] and \
        Path(real_probe["called_with"]).resolve() == secret.resolve()


def test_upload_size_cap_bites_before_the_browser_runs(tmp_path, monkeypatch):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 10)
    monkeypatch.setattr(A, "MAX_UPLOAD_FILE_BYTES", 5)
    with pytest.raises(ValueError, match="cap"):
        _validate_actions([{"upload": {"selector": "#doc",
                                          "files": [str(f)]}}])


def test_upload_normalizes_to_real_paths(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 tiny")
    out = _validate_actions([{"upload": {"selector": "#doc",
                                            "files": [str(f)]}}])
    assert out[0]["upload"]["files"] == [str(f.resolve())]


# ── 2 · 执行（receipt 行）────────────────────────────────────────────────

def test_the_receipt_row_names_what_was_uploaded():
    f = Path(__file__)
    page = _FakePage()
    pa = build_page_action([{"upload": {"selector": "input[type=file]",
                                        "files": [str(f)]}}])
    asyncio.run(pa(page))
    assert page.set_calls == [[str(f.resolve())]]
    row = pa.outcomes[0]
    assert row["ok"] is True
    assert row["uploaded"] == [f.name]
    assert row["files"] == 1


def test_a_failed_upload_says_why_in_its_row():
    class _Refusing(_FakePage):
        async def set_input_files(self, files, **kw):
            raise RuntimeError("waiting for locator input[type=file] - no element")

    page = _Refusing()
    pa = build_page_action([{"upload": {"selector": "input[type=file]",
                                        "files": [str(Path(__file__))]}}])
    asyncio.run(pa(page))
    row = pa.outcomes[0]
    assert row["ok"] is False
    assert "no element" in row["error"]


def test_upload_rides_the_same_target_grammar_as_fill(tmp_path):
    """快照索引寻址照旧可用——upload 不是二等动作。"""
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    out = _validate_actions([{"upload": {"index": 3, "files": [str(f)]}}])
    assert out[0]["upload"]["index"] == 3
    assert out[0]["upload"]["files"] == [str(f.resolve())]
