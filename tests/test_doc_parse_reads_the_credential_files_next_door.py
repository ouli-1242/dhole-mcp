"""doc_parse 读得动隔壁的凭据文件 —— 黑名单拦得住 ~/.ssh，拦不住 ~/.dhole。

症状（2026-10-06 实测，沙箱假 HOME，零网络）：

    [~/.dhole/config.json] status=200 CONTENT: {"DHOLE_HTTP_TOKEN": "sk-FAKE-BEARER", ...}
    [~/.config/gcloud/application_default_credentials.json] status=200 ...
    [~/.azure/accessTokens.json] status=200 ...
    对照组 ~/.ssh/notes.txt → Access denied

黑名单 12 目录里只有 ~/.ssh / ~/.gnupg / ~/.aws 三个用户目录。dhole 自己的秘密
（config.json 存 secret 级旋钮）与一批常见凭据存放点全在拦截面外。另一处同源
漂移：Windows 目录前缀写死 "c:\\windows"，本机 %SystemRoot%=C:\WINDOWS 所以没炸，
D 盘 Windows 安装的机器上这一条拦不住任何东西。

判据：沙箱 HOME 下的每个凭据路径都 Access denied（内容不进回执）；前缀矩阵
17+6 项全有归属；SystemRoot 派生后 D 盘安装同样被拦；正常文件（report.md 类）
仍然 200 —— 核心用途零损伤。断言停在路径判定层，不构造真实凭据路径。
"""

from __future__ import annotations

import os

import pytest

from dhole_mcp.server import MasterFetchServer, _blocked_path_prefix


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """把 ``~`` 指到临时目录 —— 凭据路径全是 fixture，一个真的都不碰。"""
    monkeypatch.setattr(
        os.path, "expanduser",
        lambda p: str(tmp_path / p.replace("~/", "")) if p.startswith("~/") else p,
    )
    return tmp_path


@pytest.mark.asyncio
async def test_doc_parse_reads_the_credential_files_next_door(sandbox_home):
    """症状本体：沙箱 ~/.dhole/config.json 被读出内容而不是拒绝。"""
    f = sandbox_home / ".dhole" / "config.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"DHOLE_HTTP_TOKEN": "sk-FAKE-BEARER"}', encoding="utf-8")
    server = MasterFetchServer()
    r = await server.doc_parse(str(f))
    assert r.error.startswith("Access denied")
    assert "sk-FAKE-BEARER" not in "\n".join(r.content)


@pytest.mark.parametrize("rel", [
    ".dhole/config.json",
    ".dhole/cdp_hosts.json",
    ".config/gcloud/application_default_credentials.json",
    ".config/gh/hosts.yml",
    ".azure/accessTokens.json",
    ".kube/config.yaml",
])
def test_the_new_credential_rooms_are_in_the_block_list(sandbox_home, rel):
    assert _blocked_path_prefix(str(sandbox_home / rel)) != ""


@pytest.mark.parametrize("rel", [
    ".ssh/id_rsa", ".gnupg/secring.gpg", ".aws/credentials",  # 旧三间，仍在
])
def test_the_old_rooms_stay_blocked(sandbox_home, rel):
    assert _blocked_path_prefix(str(sandbox_home / rel)) != ""


def test_programdata_ssh_is_blocked():
    assert _blocked_path_prefix("C:\\ProgramData\\ssh\\keys.txt") != ""


def test_the_windows_prefix_follows_systemroot(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path / "WinRoot"))
    if os.name == "nt":
        assert _blocked_path_prefix(
            str(tmp_path / "WinRoot" / "System32" / "notes.txt")) != ""
    else:
        # 非 Windows 没有 SystemRoot 语义，写死的字面量仍是拦截面的一部分
        assert _blocked_path_prefix("c:\\windows\\system32\\notes.txt") != ""


@pytest.mark.asyncio
async def test_a_normal_report_still_parses(tmp_path):
    """核心用途零损伤：普通本地文件照常 200。"""
    f = tmp_path / "report.md"
    f.write_text("# 标题\n\n正文一句话。\n", encoding="utf-8")
    server = MasterFetchServer()
    r = await server.doc_parse(str(f))
    assert r.status == 200
    assert r.content_ok is True
