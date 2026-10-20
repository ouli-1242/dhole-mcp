# -*- coding: utf-8 -*-
"""一扇门的拒绝不许有两套申报（2026-10-18 · R02-F105 · R02-123）

现场（`review/.runtime/p1/r219b_doc_parse_faces.json` · 12 面 · 全走 InMemoryTransport ·
零外网 · HEAD 3f74789）：`doc_parse` 上到拒绝面的有 10 发，行首却分两种口径 ——

  * 参数层 5 发（`offset` 负数 / `offset` 类型 / `file_path` 类型 / 陌生参数 ×2）行首交
    **装配来的**本门申报 `Nothing was fetched.`（`context.nothing_went_out()` 按 `_DOOR_KIND`
    把 `doc_parse` 登记成 read 类）；
  * 路径层 5 发（不存在 ×2 / 是目录 / 受限系统目录 / NTFS 数据流）行首**没有装配句**：
    其中 2 发是小写手抄的另一套申报 `nothing was read - the name you gave …`
    （作者住 `quality.py:1698`），另 3 发直接以下一步的散文开头（`the directory was FOUND …`、
    `the PATH was refused …` ×2），一个申报位都没占。

同一扇读的是本地文件、一个字节也没读过，却学会两种「这一发什么也没发生」—— 与 R02-089/092/100/110
是同一个形状：**行首那句申报由装配取，不许手抄**（F83 的通则，F93 定的是按作者钉不按文案钉）。

两处**自我更正**记在这里，免得账面看着像一次就对：
  * `r218` 的 41 面普查只把 `error` 以 `invalid_request:` 开头的格子当拒绝面，所以交出的
    罪名是「1 面」；r219b 量到的是同一族的 **5 面**（4 个分支）。R02-123 的半径按后者记账。
  * `r219` 第一版的夹具错（B 层工具错）：它把「NTFS 数据流」「不支持的扩展名」喂成**不存在的路径**，
    两发都掉进 `File not found` 分支（存在性检查在流名/扩展名之前，是代码顺序不是缺陷）。
    r219b 真挂了一条流、真写了一颗 `.mp3` 才分清是哪一支。旧档一字不动留着。

**边界（本文件也钉这一半）**：文件**读过**之后才失败的格子不许在行首说「这一发什么也没发生」，
那是假话 —— 所以这一把改的是「读取之前被拒」那一族（4 支），不是 `parse` 门下的整块。
`不支持的扩展名` 那一发同样是读取之前被拒，可它的句子由 `parse` 的 `way_forward` 写（走 else 支），
换作者就得先解决「怎么把读成功与读之前被拒分开」，另立一颗（见 fix 笔记），本文件不为它编判据。
"""

from __future__ import annotations

import json
import sys

import pytest

from dhole_mcp import quality as quality_mod
from dhole_mcp import server as server_mod
from dhole_mcp import context as context_mod
from dhole_mcp.context import nothing_went_out, tool_scope
from dhole_mcp.server import MasterFetchServer

with tool_scope("doc_parse"):
    CLAUSE = nothing_went_out()

DOC_BODY = "AAA1 BBB2 CCC3 DDD4 EEE5 FFF6\n" + "x" * 40


@pytest.fixture(scope="module")
def doc_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("clause-fixture") / "body.txt"
    p.write_text(DOC_BODY, encoding="utf-8")
    return str(p)


@pytest.fixture(scope="module")
def root_dir(tmp_path_factory):
    return str(tmp_path_factory.mktemp("clause-fixture"))


async def _call(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("doc_parse", args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


#: 读取**之前**被拒的这一族 + 参数层那一族：10 发都该说同一句第一句
FACES = [
    ("路径不存在(绝对)", lambda p, r, t: {"file_path": str(t / "nope.txt")}),
    ("路径不存在(相对)", lambda p, r, t: {"file_path": "nope-relative.txt"}),
    ("路径是目录", lambda p, r, t: {"file_path": r}),
    ("受限系统目录", lambda p, r, t: {"file_path": r"C:\Windows\System32\drivers\etc\hosts"}),
    ("offset 负数", lambda p, r, t: {"file_path": p, "offset": -6}),
    ("offset 类型错", lambda p, r, t: {"file_path": p, "offset": "abc"}),
    ("file_path 类型错", lambda p, r, t: {"file_path": 7}),
    ("陌生参数", lambda p, r, t: {"file_path": p, "not_an_argument": 1}),
    ("limit 其实不认识", lambda p, r, t: {"file_path": p, "limit": -1}),
]


@pytest.mark.parametrize("label,make", FACES)
@pytest.mark.asyncio
async def test_every_refusal_face_of_this_door_opens_with_the_same_sentence(
        label, make, doc_path, root_dir, tmp_path):
    """同一扇的拒绝面只许有一套申报，且那句是装配来的那一句。

    修之前：路径层 5 发行首要么是小写手抄的 `nothing was read - …`，要么根本没有申报；
    参数层 5 发行首是 `Nothing was fetched.`。
    """
    payload = await _call(make(doc_path, root_dir, tmp_path))
    na = str(payload.get("next_action") or "")
    assert str(payload.get("error") or ""), f"{label} 这一发本该到拒绝面: {payload}"
    assert na, f"{label} 的拒绝没有出路: {payload.get('error')}"
    assert na.startswith(CLAUSE), (label, f"第一句不是装配来的那一句: {na[:150]}")
    assert na.split(". ")[0] == CLAUSE.rstrip("."), (label, na[:120])
    assert "nothing was read" not in na.lower(), (
        f"{label} 还说着一套不同的申报: {na[:150]}")


@pytest.mark.skipif(sys.platform != "win32", reason="ADS 流语法只在 NTFS 上存在")
@pytest.mark.asyncio
async def test_the_stream_face_opens_with_the_same_sentence(doc_path):
    """第 10 颗拒绝面（真流）单独一条：它的格子依赖 NTFS，其余 9 颗不依赖。

    流得**真挂着**才走到 `alternate data stream` 那一支 —— r219 第一版没挂流，
    那一发掉进 `File not found`，账面就成了「同一支有 4 个面」。
    """
    stream = str(doc_path) + ":marker"
    with open(stream, "w", encoding="utf-8") as fh:
        fh.write("hidden stream")
    payload = await _call({"file_path": stream})
    na = str(payload.get("next_action") or "")
    assert str(payload.get("error") or "").startswith("Access denied")
    assert na.startswith(CLAUSE), na[:150]
    assert "nothing was read" not in na.lower(), na[:150]


@pytest.mark.parametrize("label,make,keeps", [
    ("路径不存在", lambda p, t: {"file_path": str(t / "nope.txt")},
     ("name you gave", "places it tried", "re-sending the same string")),
    ("路径是目录", lambda p, r: {"file_path": r},
     ("directory was FOUND", "one file per call")),
    ("受限系统目录", lambda p, r: {"file_path": r"C:\Windows\System32\drivers\etc\hosts"},
     ("PATH was refused", "guard, not a knob")),
])
@pytest.mark.asyncio
async def test_the_guidance_survives_its_new_head(label, make, keeps, doc_path, tmp_path):
    """换的是行首那半句申报，不是整段重写 —— 每一支的指引话一句都不许少。

    这些句子各自是别的评审修出来的（R02-062 的「别替绝对路径建议变绝对」、目录那支的
    「路径没毛病」、守卫那支的「这是门不是旋钮」）。本把只把申报位交回作者。
    """
    payload = await _call(make(doc_path, tmp_path))
    na = str(payload.get("next_action") or "")
    for phrase in keeps:
        assert phrase.lower() in na.lower(), (label, phrase, na[:220])


@pytest.mark.asyncio
async def test_a_file_that_was_read_does_not_claim_that_nothing_happened(tmp_path):
    """边界另一半：读成功而内容坏掉的那一发**不许**在行首说「这一发什么也没发生」。

    这一条是防着把装配句一把贴在 `parse` 门整块上的 —— 那句对它是假话（文件确实读了）。
    """
    bad = tmp_path / "broken.json"
    bad.write_text("{ this is not json ]", encoding="utf-8")
    payload = await _call({"file_path": str(bad)})
    na = str(payload.get("next_action") or "")
    assert str(payload.get("error") or ""), payload
    assert not na.startswith(CLAUSE), f"读过内容的格子被安了一句假申报: {na[:150]}"
    assert "found and read" in na, na[:200]


@pytest.mark.asyncio
async def test_the_head_sentence_is_taken_from_the_assembler(
        doc_path, root_dir, tmp_path, monkeypatch):
    """归属锁（F93 的通则）：把装配换成哨兵，10 面里的每一颗行首都跟着变。

    哨兵贴在**作者本人**身上（`context.nothing_went_out`）：参数层与 server 那几处在调用时才
    import 它，跟着走；`quality.py` 是模块顶层绑定的唯一取词者，所以那一处也一起换。
    抄在原地的句子不会因为换作者而变 —— 那正是这一条要抓的。
    """
    sentinel = "SENTINEL-DECLARATION."

    def _fake(_door: str = "") -> str:
        return sentinel

    monkeypatch.setattr(context_mod, "nothing_went_out", _fake)
    monkeypatch.setattr(quality_mod, "nothing_went_out", _fake)
    monkeypatch.setattr(server_mod, "_nothing_went_out", lambda *a, **k: sentinel)
    heads = {}
    for label, make in FACES:
        payload = await _call(make(doc_path, root_dir, tmp_path))
        heads[label] = str(payload.get("next_action") or "")
    for label, na in heads.items():
        assert na.startswith(sentinel), (label, f"这一面的行首不是取来的: {na[:150]}")


def test_the_source_of_this_door_no_longer_hand_copies_a_declaration():
    """源码级锁：`_agent_hints` 的**字面量**里不许再有手抄的申报。

    只数字符串常量、不数注释（`ast` 走一遍）：这一支的注释里逐字抄着修之前的那句，
    按行扫会把证据本身判成复发 —— r215 的普查正是在这里错过三颗假阳性。
    判据取**行首那一格**（`Nothing was read …`），不取「句中出现」：同一函数里 CDP 那两处长句
    说的是「浏览器没应答，所以既没抓到也没读到」，那是另一扇门、另一位作者的另一件事，
    r218 的 41 面普查里没有一条回执证明它们害到谁 —— 按 §12 不为量不到的句子改文案，
    尺子也不把没量到的形状算成战果。
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(quality_mod._agent_hints))
    strings = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    hand_copied = [s for s in strings
                   if s.lower().lstrip().startswith("nothing was read")]
    assert hand_copied == [], (
        f"这些文案把申报写回了行首字面量（该由 `nothing_went_out()` 装配）: "
        f"{hand_copied[:1]}")
    assembly = inspect.getsource(quality_mod._agent_hints)
    assert "nothing_went_out()" in assembly, "这一族的行首申报没有作者"
