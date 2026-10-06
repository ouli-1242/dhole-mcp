r"""NTFS 备用数据流读得出来 —— 扩展名门被流名骗过。

症状（2026-10-06 实测，tmp_path 夹具）：

    [report.txt:hidden.json (ADS)] status=200 error='' CONTENT: {"ads_secret": "FAKE-ADS-SECRET"}
    splitext of ADS path: '.json'

后缀判定取流名最后一段，report.txt:hidden.json 被当成一个 .json 文件读出来；
realpath 不剥离流名，路径守卫同样不感知流语法。利用前提是目标机器上确实存在
带内容的流（浏览器的 Zone.Identifier、软件隐藏数据），半径有限，但「读一个
看得见的文件」的模型在这里失真：请求者点名的文件是 report.txt，读到的却是
流里的另一份内容。

判据：流语法路径回 Access denied 且流内容不进回执（夹具流真实存在，若不拒
就会泄）；正常盘符路径（C:\…）不受影响。POSIX 上冒号是合法文件名字符，同一
拒绝条件不得在非 NTFS 上启用 —— 否则伤核心用途。
"""

from __future__ import annotations

import os
import sys

import pytest

from dhole_mcp.server import MasterFetchServer


@pytest.mark.skipif(sys.platform != "win32", reason="ADS 流语法只在 NTFS 上存在")
@pytest.mark.asyncio
async def test_an_ntfs_stream_path_is_refused_instead_of_read(tmp_path):
    base = tmp_path / "report.txt"
    base.write_text("benign cover file", encoding="utf-8")
    stream = str(base) + ":hidden.json"
    with open(stream, "w", encoding="utf-8") as f:  # 真造一个流：不拒就会泄
        f.write('{"ads_secret": "FAKE-ADS-SECRET"}')
    r = await MasterFetchServer().doc_parse(stream)
    assert r.error.startswith("Access denied")
    assert "FAKE-ADS-SECRET" not in "\n".join(r.content)


@pytest.mark.skipif(sys.platform != "win32", reason="盘符冒号只在 Windows 上是流语法歧义")
@pytest.mark.asyncio
async def test_a_normal_drive_letter_path_is_untouched(tmp_path):
    f = tmp_path / "plain.json"
    f.write_text('{"ok": true}', encoding="utf-8")
    r = await MasterFetchServer().doc_parse(str(f))
    assert r.status == 200
    assert r.content_ok is True


@pytest.mark.asyncio
async def test_a_colon_filename_on_this_platform_stays_consistent(tmp_path):
    """POSIX 上冒号是合法文件名 —— 拒绝条件必须只在 nt 生效（核心用途零损伤）。"""
    f = tmp_path / "weird:name.json"
    f.write_text('{"ok": true}', encoding="utf-8")
    r = await MasterFetchServer().doc_parse(str(f))
    if os.name == "nt":
        assert r.error.startswith("Access denied")
    else:
        assert r.status == 200
