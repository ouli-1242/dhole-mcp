"""一句话服务三种形状：出路必须由写那句 `error` 的人交出来。

现场（2026-10-15 · P4 写 F12 时实测，三发同一棵树、同一颗 `doc_parse` · R02-022 · P2 · 组 C）：

    doc_parse(file_path=<CFB 的 legacy.doc>)
      → error：「This file is Word 97-2003 (.doc) - OLE2/CFB … renaming it does not change
        the bytes. Convert it first - `soffice --headless --convert-to docx <file>` …」
      → next_action：「… An unsupported extension is fixed by **the rename the error
        suggests**; …」

    doc_parse(file_path=<notes.log>)
      → error：「Unsupported file type '.log'. Supported: … If the file is plain text under
        another extension, copy or rename it to .txt (or .md) and parse that.」
      → next_action：同一句转述

    doc_parse(file_path=<broken.json>)
      → error：「.json file is not valid JSON: Expecting property name …」
      → next_action：还是那一句谈扩展名与改名的话

三种形状共用一句**通用兜底**，而那一句在第一种里正好与 `error` 相反：error 说"改名不改字节"，
next_action 说"按 error 建议的改名去修"——而 error 从没建议过改名。第二种它只是**转述**另一个
作者的话（转述就是第二个作者：下一次 parse.py 改口，这里不会跟着改）。第三种它答的根本不是
被问的问题（坏 JSON 与扩展名无关）。这是组 C 那条纪律的标准病例：**建议的作者必须与错误的
作者是同一个人**。

修的形状不是"给那句话加一个 if"，而是把出路的作者放回 error 旁边：
parse 的两个拒绝点各自交出自己的那句话（`way_forward`），信封上有作者的话时 `quality.py`
就闭嘴；没有作者的话时才用通用句，而通用句从此**不许**声称 error 建议了什么。

契约（本文件钉住）：
  * CFB 那一发的 next_action 必须带着它自己的作者的话（先转换），并且不出现
    「the rename the error suggests」；
  * 未支持扩展名那一发必须与 parse 自己那句同文（改名成 `.txt`），不是转述；
  * 坏 JSON 那一发不许被 lecture 扩展名/改名，通用句只说它真知道的那半（文件被读过、
    坏的是内容、路径没什么可修）；
  * 三发的判决位一位不动：`status=0` / `content_ok=False` / `fetcher_used="parse"`；
  * 反面锁：通用句不能因为"有作者了"就整个消失——没有作者的话时它必须还在（否则调用方
    拿到空 `next_action`，是另一种把话藏起来）；
  * `quality.py` 里那句转述在 src 中归零，手册里也不许留同义抄本。
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib

import pytest

from dhole_mcp import quality as quality_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

GENERIC_PHRASE = "the rename the error suggests"

#: OLE2 / Compound File Binary 魔数：`_sniff_cfb` 只读前 8 字节，后面填零即可。
_CFB_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


def _parse(tmp_path, name: str, *, data: bytes | None = None,
           text: str | None = None):
    path = tmp_path / name
    if data is not None:
        path.write_bytes(data)
    else:
        path.write_text(text or "", encoding="utf-8")
    return asyncio.run(MasterFetchServer().doc_parse(file_path=str(path)))


# ─── 1 · 三种形状各由自己的作者说话 ───


@pytest.mark.parametrize("kind", ["cfb", "unsupported", "badjson"])
def test_the_verdict_positions_do_not_move(tmp_path, kind):
    r = {
        "cfb": lambda: _parse(tmp_path, "legacy.doc", data=_CFB_HEADER),
        "unsupported": lambda: _parse(tmp_path, "notes.log", text="一份普通文本"),
        "badjson": lambda: _parse(tmp_path, "broken.json", text="{not json at all"),
    }[kind]()
    assert r.status == 0, kind
    assert r.content_ok is not True, kind
    assert r.fetcher_used == "parse", kind
    assert r.error, kind


def test_the_cfb_way_forward_is_written_by_the_refusal_itself(tmp_path):
    r = _parse(tmp_path, "legacy.doc", data=_CFB_HEADER)
    assert "renaming it does not change the bytes" in r.error, r.error
    na = r.next_action or ""
    assert GENERIC_PHRASE not in na, (
        f"出路还在替本发 error 声称它建议过改名：{na!r}")
    assert "Convert it first" in na or "soffice" in na, (
        f"拒绝自己写了怎么转换，出路却没带上那一句：{na!r}")


def test_the_unsupported_extension_shares_its_author_wording(tmp_path):
    r = _parse(tmp_path, "notes.log", text="一份普通文本")
    assert "rename it to .txt" in r.error, r.error
    na = r.next_action or ""
    assert GENERIC_PHRASE not in na, na
    # 不是转述：parse 自己那句的关键词必须在出路里原样出现。
    assert ".txt" in na, f"出路把改名那句话说丢了：{na!r}"


def test_invalid_json_is_not_lectured_about_extensions(tmp_path):
    r = _parse(tmp_path, "broken.json", text="{not json at all")
    na = (r.next_action or "").lower()
    assert "extension" not in na and "rename" not in na, (
        f"坏 JSON 的出路在谈扩展名：{r.next_action!r}")
    assert "the file was found and read" in na, (
        f"通用句被删空了——调用方拿不到任何下一步：{r.next_action!r}")


# ─── 2 · 那句话在树里归零 ───


def test_the_paraphrase_has_been_erased_from_the_hint_author():
    src = inspect.getsource(quality_mod)
    assert GENERIC_PHRASE not in src, "quality.py 还在替别人的 error 说话"


def test_the_manual_carries_no_copy_of_the_paraphrase():
    root = (pathlib.Path(inspect.getsourcefile(server_mod)).resolve().parent
            / "skills" / "dhole-web")
    for path in sorted(root.rglob("*.md")):
        body = path.read_text(encoding="utf-8")
        assert GENERIC_PHRASE not in body, f"{path.name} 留着一份转述抄本"


# ─── 3 · 出路不是空字段：通用句仍在原位 ───


def test_the_fallback_sentence_still_exists_for_authors_less_failures(tmp_path):
    """没有作者的话时，通用句必须还在——但只说它真知道的那半。"""
    r = _parse(tmp_path, "broken.json", text="{not json at all")
    na = r.next_action or ""
    assert "nothing to fix about the path" in na.lower(), na
    assert "error" in na.lower(), "通用句仍要点名 error 才是那一发的判决"
