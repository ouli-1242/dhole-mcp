"""非 Office 的 CFB 容器不是"没转成 docx 的文档"：出路不许指定一个文档转换目标。

现场（2026-10-15 · P4 写 R02-F12 复验时量到的同族第三问 · R02-043 · P3 · 组 C）：

    doc_parse(file_path=<OLE2/CFB 开头的 installer.msi>)
      → error：「This file starts with the OLE2/CFB signature: a pre-2007 Office document,
        **or another CFB container (msi, msg, ...) - not a document format this reader
        opens**. There is no text layer a reader can walk here, and renaming it does not
        change the bytes. …」
      → next_action：「Convert it first - `soffice --headless --convert-to docx <file>`
        (LibreOffice), or the authoring app's 'Save As .docx' - then parse the result.」

同一封响应：error 说"这不是本文读取器认识的文档格式"，出路却把它当 Word 教它另存为 `.docx`。
 installer.msi 转不成 docx，而一句"先转换"对它是零信息 —— 调用方照着做只会再撞一次。

根因在 `parse._cfb_refusal` 的默认值：`_CFB_LEGACY.get(ext)` 未命中时把 `target` 兜成
`"docx"`，然后复用同一句 `_cfb_way_forward(target)`。已知的那三对（.doc/.xls/.ppt 与改名成
zip 后缀的两支）指对了目标，**未知容器**这一支因此被指错。R02-F12 之前这句话只挂在 error
的尾巴上；把它搬进 next_action 之后，它升格成"唯一的下一步"，错得更显眼 —— 那也是那一刀
如实记下这一问的原因。

契约（本文件钉住）：
  * 未知 CFB 容器（.msi / .msg / 裸魔数其它后缀）的出路不许出现文档转换目标
    （`--convert-to docx`、`Save As .docx`），要说的是"本文读取器不开这种容器"，
    并把目标交还给调用方的问题本身（要取什么就说要取什么）；
  * **反面锁**：已知的三对映射一位不动 —— `.doc`→`docx`、`.xls`→`xlsx`、`.ppt`→`pptx`
    的转换命令照旧点名（这是 R01 那把刀立住的真出路，不许因为这把就一起删哑）；
  * 两支的判决位不动：拒绝仍是拒绝（`error` 非空、`content` 空、`status=0` /
    `content_ok` 不为 true），`renaming it does not change the bytes` 那半句不许掉；
  * 目标名只能有一位作者：已知映射查 `_CFB_LEGACY`，未知容器**没有**目标 —— 不许在句子
    里再兜一个默认值。
"""

from __future__ import annotations

import pathlib

import pytest

from dhole_mcp.parse import _cfb_refusal, parse_file_detailed

#: OLE2 / CFB 魔数：`_sniff_cfb` 只读前 8 字节，后面填零即可。
CFB = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


def _write(tmp_path, name: str) -> str:
    path = pathlib.Path(tmp_path) / name
    path.write_bytes(CFB)
    return str(path)


# ─── 1 · 未知容器不许被指定一个文档目标 ───

@pytest.mark.parametrize("name", ["installer.msi", "mail.msg", "shell.bin"])
def test_an_unknown_container_is_not_told_to_save_it_as_a_document(tmp_path, name):
    err, way = _cfb_refusal(pathlib.Path(name).suffix)
    assert way, "拒绝不能没有出路"
    assert "docx" not in way and "xlsx" not in way and "pptx" not in way, way
    assert "soffice" not in way, way
    assert "cannot be opened" in way or "not a document" in way.lower(), way


def test_the_unknown_container_verdict_still_refuses_cleanly(tmp_path):
    path = _write(tmp_path, "installer.msi")
    content, err, _extras = parse_file_detailed(path)
    assert content == "" and err, (content[:40], err[:80])
    assert "another CFB container (msi, msg, ...)" in err, err
    assert "renaming it does not change the bytes" in err, err


# ─── 2 · 已知映射一位不动（反面锁） ───

@pytest.mark.parametrize("ext,target", [(".doc", "docx"), (".xls", "xlsx"), (".ppt", "pptx")])
def test_the_known_legacy_pairs_keep_their_conversion_target(ext, target):
    err, way = _cfb_refusal(ext)
    assert f"--convert-to {target}" in way, (ext, way)
    assert "Save As" in way, way
    assert "renaming it does not change the bytes" in err, err


def test_a_renamed_pair_names_the_format_it_actually_is():
    """改名成 .docx 的 CFB（最常见的自救尝试）仍然指回 docx，且说清真格式。"""
    err, way = _cfb_refusal(".docx")
    assert "named .docx but is a pre-2007 Word binary" in err, err
    assert "--convert-to docx" in way, way


# ─── 3 · 目标只有一位作者 ───

def test_the_way_forward_target_comes_only_from_the_known_map():
    """兜底目标从函数里消失：未命中映射 = 没有目标，而不是"默认 docx"。"""
    import inspect

    from dhole_mcp import parse as parse_mod
    src = inspect.getsource(parse_mod._cfb_refusal)
    assert 'target = "docx"' not in src, src
    assert "else" in src, "未命中映射那一支必须自己有一条出路，而不是共用文档那句"
