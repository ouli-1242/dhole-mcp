"""建议不许教人传一颗**那扇门根本不收**的名字（R02-082 · P2）。

17.0 §4 把「只要地图」从 `web_crawl` 的参数升成了独立工具 `web_discover`；`discover_only` 从此
只剩响应侧身份。仓库在升格时改过一处（`test_a_map_the_budget_cut_can_be_read_back_row_by_row.py`
的 `TestTheCursorIsReachableFromOutsideTheProcess` 钉住了游标那一句），但**另外三处没跟着改**：

  · `crawl.py:1327`（sitemap 地图的收尾建议）与 `crawl.py:2295`（link-walk 地图的收尾建议）都写
    "re-run web_crawl with discover_only=false"；
  · `references/fields.md` 的 `discover_only` 行（`sys_guide` 真送出去的那一卷）写 "or re-run with
    `discover_only=false`"。

真机读数（`review/.runtime/p1/r84_discover_only.json` · 本机靶站 · 8 发）：同一发只差那颗名字时，
`web_crawl(discover_only=false)` 与 `(=true)` 都被 dispatch 当场拒，`web_discover(discover_only=true)`
也被拒；把名字去掉就通过。也就是说这三句教的动作**在两扇门上都没有路径** —— 调用方照做先吃一次
拒绝，而拒绝文案自己列出的支持清单里根本没有这个名字。房内这条规矩由 D1 那笔账立过
（`server.py:3457` `_knob_here` 的注释：建议不许点名一颗调用方传不进来的参数）。

所以这一族要的是守卫，不是"再改一处"：三格按**文本面**扫（源码里的建议散文、手册语料、常驻层的
工具描述），第四格按**真门**守前提 —— 那颗名字今天确实传不进去；哪天它又变成可传的，改的是规则，
不是让一条永绿的守卫继续假装在看。
"""

from __future__ import annotations

import io
import re

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp import guide
from dhole_mcp.server import MasterFetchServer, _TOP_LEVEL_ARGS

#: 只在响应里活着、两扇门的 wire 面都不收的一颗名字（R02-082 的那一位）。
RESPONSE_ONLY = "discover_only"

_TOOL_NEAR = re.compile(r"\bweb_(read|crawl|discover|search|act|request|resolve)\b")


def _names_the_door_wrong(lines, label):
    """"提到某扇 web_ 门之后要求传 discover_only=" 的片段。"""
    bad = []
    for where, text in lines:
        for m in re.finditer(rf"\b{RESPONSE_ONLY}\s*=", text):
            window = text[max(0, m.start() - 90):m.start()]
            hit = _TOOL_NEAR.findall(window)
            if hit:
                bad.append(f"{label} {where}: 提到 web_{hit[-1]} 之后要求传 {RESPONSE_ONLY}=")
    return bad


class TestTheAdviceNeverNamesAnUnreachableSwitch:

    def test_the_map_advice_in_crawl_does_not(self):
        """两处地图建议串不许再把调用方送去 `web_crawl(discover_only=…)`。

        只扫建议散文这一种形状：`discover_only=True` 作为内部 kwarg 在这份文件里合法地出现几十次
        —— 按裸名字扫就是本次缺陷描述里那条"grep 一次就误判"的同款陷阱。
        """
        src = io.open(crawl_mod.__file__, encoding="utf-8").read().splitlines()
        hits = _names_the_door_wrong(list(enumerate(src, 1)), "crawl.py")
        assert not hits, "地图上那句 discover_only 在门外走不通：\n" + "\n".join(hits)

    def test_the_handbook_does_not(self):
        """`sys_guide` 送出去的每一卷都不许教一颗两扇门都不收的名字。"""
        lines = []
        for path in sorted(guide._ROOT.rglob("*.md")):
            for i, line in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
                lines.append((f"{path.name}:{i}", line))
        hits = _names_the_door_wrong(lines, "手册")
        assert not hits, "手册在教一件真机上做不到的事：\n" + "\n".join(hits)

    def test_the_wire_descriptions_do_not(self):
        """常驻层那份（工具描述与每颗参数的描述）同一位作者，同一把尺。"""
        lines = []
        for d in MasterFetchServer._TOOL_DEFS:
            lines.append((d["name"], d.get("description") or ""))
            for key, v in (d.get("inputSchema", {}).get("properties") or {}).items():
                lines.append((f"{d['name']}.{key}", v.get("description") or ""))
        hits = _names_the_door_wrong(lines, "wire")
        assert not hits, "\n".join(hits)


class TestThePremiseIsStillTrue:
    """前提：那颗名字今天确实进不了这两扇门。前提变了要改规则，不许留一条防空气的守卫。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["web_crawl", "web_discover"])
    async def test_dispatch_refuses_it_on_both_doors(self, tool):
        """真机读数与这里必须同一种形状：dispatch 是**抛**拒绝，不是回一颗 error 字段。

        （r84 量到的是 `_reject_unknown_args` 冒出的 `ArgumentRefusal`；本把这格第一次写成
        "读 payload 的 error"，于是它把一次正确的拒绝读成了一次失败 —— 记 C 类，改成按异常判。）
        """
        from dhole_mcp.params import ArgumentRefusal

        srv = MasterFetchServer()
        with pytest.raises(ArgumentRefusal) as got:
            await srv._dispatch(tool, {"url": "https://example.invalid/",
                                       RESPONSE_ONLY: False})
        assert RESPONSE_ONLY in str(got.value), (
            f"{tool} 的拒绝没点名 {RESPONSE_ONLY}：这一格的判据要跟着改")
        assert RESPONSE_ONLY not in _TOP_LEVEL_ARGS[tool], \
            f"{tool} 的 dispatch 名单里又出现了 {RESPONSE_ONLY}"
