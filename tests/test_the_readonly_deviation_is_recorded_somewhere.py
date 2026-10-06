"""readOnly 的声明是真的，但「宿主自动放行」的场景里提示与围栏的偏差没记录在案。

症状（2026-10-06 F-D-J4）：web_read/web_crawl/web_discover/feed_read 四颗在
wire 上都带 readOnlyHint:true——只发 GET、本地只写进程内簿记、CDP 首挂有
confirm 围栏，三条辩护全有代码。残余：ignore_robots 没有任何人工门；一个据
hints 自动放行 readOnly 工具的宿主，会让它成为唯一闸门的豁免钥匙。这个偏差
此前无任何文档或守卫说明。

判决（B 档，纯语料刀）：不改提示（它们是真话），在 behavior 卷（tools.md）
补一段把三重辩护与偏差写在一起。各宿主是否据 hints 自动放行 readOnly 是外部
一手读数（§9.2 停机点，网页搜索不算数）；本刀只保证「偏差有册可查」。
"""

from __future__ import annotations

import pathlib

_TOOLS_MD = (pathlib.Path(__file__).resolve().parents[1] / "src"
             / "dhole_mcp" / "skills" / "dhole-web" / "references"
             / "tools.md").read_text(encoding="utf-8")


def test_the_readonly_deviation_is_recorded_in_the_behavior_volume():
    assert "readOnlyHint" in _TOOLS_MD, "behavior 卷没提 readOnly 的声明"
    for leg in ("GET", "cache", "cdp_authorization_required"):
        assert leg in _TOOLS_MD, f"三重辩护缺一条：{leg}"
    assert "ignore_robots" in _TOOLS_MD
    assert "auto-approv" in _TOOLS_MD or "automatically approve" in _TOOLS_MD, (
        "自动放行场景下的偏差（合规判断自负其责、无人再问）没有记录在案")
