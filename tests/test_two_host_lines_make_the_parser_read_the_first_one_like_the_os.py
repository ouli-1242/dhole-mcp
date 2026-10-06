"""同域名两行 hosts 记录，解析器读的是最后一行而 OS 读第一行 —— 黑洞钉位可被翻成元数据钉位。

症状（2026-10-06 实测，stub hosts 文件，零网络）：

    pin[dup.evil.test] = 169.254.169.254 (OS resolvers use the FIRST line = 0.0.0.0;
    parser took the LAST)
    PASSED (no SecurityError) <- http://dup.evil.test/
    对照组（单行 0.0.0.0 钉位）→ REFUSED ... hosts-pinned blackhole

`table[name] = ip` 后行覆盖前行。glibc 与 Windows 的 hosts 语义都是首行赢：
黑洞钉位（0.0.0.0）在上、元数据钉位在下的文件，OS 拒绝、dhole 放行 —— 同一台
机器对同一个名字给出两个相反的答案。利用前提是能写 hosts 文件（通常需管理员），
故 Severity 定 Low，但解析器不该与 OS 各说各话。

判据：两行场景 `_hosts_file_pin` 返回首行值，0.0.0.0 在上的黑洞判定重新生效；
单行/多主机名/注释/尾点行为不变（test_audit_fixes.py 的解析器用例保持绿）。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def hosts_parser(tmp_path, monkeypatch):
    sp = __import__("dhole_mcp.security", fromlist=["_hosts_file_pin"])

    def install(content: str):
        hosts = tmp_path / "hosts"
        hosts.write_text(content, encoding="utf-8")
        monkeypatch.setattr(sp, "_hosts_file_path", lambda: str(hosts))
        monkeypatch.setattr(sp, "_hosts_cache", None)
        return sp

    return install


def test_two_host_lines_make_the_parser_read_the_first_one_like_the_os(hosts_parser):
    sp = hosts_parser(
        "0.0.0.0 dup.evil.test\n"
        "169.254.169.254 dup.evil.test\n"
    )
    assert sp._hosts_file_pin("dup.evil.test") == "0.0.0.0"


def test_the_blackhole_pin_verdict_survives_a_second_line(hosts_parser):
    """0.0.0.0 钉位在前、元数据在后的文件，复验层把它读成黑洞而不是元数据。"""
    from dhole_mcp.security import _resolves_to_internal
    hosts_parser(
        "0.0.0.0 blackholed.tracker.test\n"
        "169.254.169.254 blackholed.tracker.test\n"
    )
    internal = _resolves_to_internal("blackholed.tracker.test")
    assert internal, "hosts 钉位 0.0.0.0 应被读成 internal（黑洞判定生效）"


def test_single_line_and_multi_alias_behavior_is_untouched(hosts_parser):
    sp = hosts_parser(
        "# comment line\n"
        "127.0.0.1   pinned.example  SECOND.example.\n"
        "0.0.0.0 ad.example # trailing comment\n"
        "malformed-line\n",
    )
    assert sp._hosts_file_pin("pinned.example") == "127.0.0.1"
    assert sp._hosts_file_pin("second.example") == "127.0.0.1"
    assert sp._hosts_file_pin("AD.EXAMPLE") == "0.0.0.0"
    assert sp._hosts_file_pin("not-pinned.example") is None
