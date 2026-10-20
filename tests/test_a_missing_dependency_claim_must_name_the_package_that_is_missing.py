"""「你缺依赖」这句话只能用在真的缺依赖上。

现场（2026-10-15 · P4 写 R02-F8 的真机复验里撞出来的 R02-040 · P2 · 组 C）：

    # 把一颗符号从已导入的模块里摘掉（半重载的确定形状），再解析一份普通的 .txt
    del dhole_mcp.charset.contested_rival
    parse_file_detailed("note.txt")
      → error=「Missing dependency for .txt parsing: cannot import name
        'contested_rival' from 'dhole_mcp.charset' (D:\\tools\\dhole-mcp\\src\\dhole_mcp\\charset.py).
        python-docx is installed - the parse failure was not a missing dependency
        (corrupt file? wrong extension?)」

一句话里两次自我否认：先说"缺依赖"，下一行说"不是缺依赖"。而 `content_ok=false` 把调用方
支去装一颗**根本不缺**的包（那句 install 建议还指向 python-docx，而他交的是一份 .txt）。

根因是一行的形状问题：`parse_file_detailed` 的 `except ImportError` 罩着**整个分派块**，
块里既有第三方 import（docx / openpyxl / pdf），也有 dhole 自己的 `_read_text_file` /
`_parse_csv` / 从 `.charset` 取符号。任何一颗 ImportError 都落进同一句"Missing dependency
for {ext} parsing"。两种 ImportError 在 Python 里本来就能分开看：

    from dhole_mcp.charset import contested_rival   → ImportError, e.name='dhole_mcp.charset'
    import some_absent_package_xyz                  → ModuleNotFoundError, e.name='some_absent_package_xyz'

`e.name` 的第一段是不是 `dhole_mcp`，就是"谁缺东西"的答案。

契约（本文件钉住）：
  * 内部 import 失败不许说"缺依赖"，也不许交 install 建议 —— 要说这是 dhole 自己的失败、
    点名是哪个模块、并给出调用方真能做的事（报修 / 重启进程），不许让人去装包；
  * 真的缺第三方包那一支**照旧**点名缺的那颗并给 install 建议 —— 本刀不许把真话一起删掉
    （反面锁），且建议里不许冒出和这一发无关的包名（从前 .xlsx 的失败听人说 python-docx）；
  * 包在盘上、失败发生在它内部（`cannot import name … from 'docx'`）时说的是版本不匹配，
    既不叫"缺依赖"也不叫人去装；
  * `e.name` 为空时这句话没有资格点任何一颗包的名 —— 只能承认这一发不知道是谁缺东西；
  * 同一封响应里不许既说"Missing dependency"又说"not a missing dependency"（家族守卫：
    判决的因果词不许超出这一发知道的）；
  * 判决位不动：失败仍然是失败（`error` 非空、`content` 空、`content_ok` 不为 true）——
    本刀修的是那句话，不是把内部错误改记成成功。
"""

from __future__ import annotations

import importlib
import pathlib
import tempfile

import pytest

from dhole_mcp import charset as charset_mod
from dhole_mcp import parse as parse_mod
from dhole_mcp.parse import parse_file_detailed


@pytest.fixture
def txt_file():
    d = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-"))
    f = d / "note.txt"
    f.write_text("hello 正文 here\nsecond line\n", encoding="utf-8")
    return str(f)


@pytest.fixture
def half_reloaded_charset():
    """半重载的确定形状：模块在 sys.modules 里，但那颗符号没了。

    （新进程里 `from .charset import X` 撞上的是同一句 ImportError —— 区别只在谁把
    邻居留下了旧版本，本文件不演那段历史，只钉这一句报告。）
    """
    saved = getattr(charset_mod, "contested_rival")
    del charset_mod.contested_rival
    try:
        yield
    finally:
        charset_mod.contested_rival = saved
        importlib.reload(charset_mod)


def test_an_internal_import_failure_is_not_reported_as_a_missing_dependency(
        txt_file, half_reloaded_charset):
    _content, err, _extras = parse_file_detailed(txt_file)
    assert err, "内部 import 失败被吞掉了"
    assert "Missing dependency" not in err, err
    assert "dhole_mcp.charset" in err, f"报告没点名是哪颗模块失败：{err!r}"
    assert "contested_rival" in err, f"报告没点名是哪颗符号：{err!r}"
    assert "pip install" not in err and "install" not in err.lower(), (
        f"没有东西缺装，却把人支去装包：{err!r}")


def test_the_two_sentences_never_disagree(txt_file, half_reloaded_charset):
    """家族守卫：因果词不许超出这一发知道的。"""
    _content, err, _extras = parse_file_detailed(txt_file)
    lowered = err.lower()
    assert not ("missing dependency" in lowered and "not a missing dependency" in lowered), err


def test_the_verdict_positions_do_not_move(txt_file, half_reloaded_charset):
    content, err, _extras = parse_file_detailed(txt_file)
    assert err and content == "", (content[:40], err[:80])
    assert content == "", "内部失败被改记成成功就是弱化门禁"


def test_a_genuinely_missing_package_is_named_and_priced(monkeypatch, txt_file):
    """反面锁：真缺依赖那一支不许被本刀删哑 —— 点名缺的那颗，并给装它的命令。"""
    def _raise(_path):
        raise ModuleNotFoundError("No module named 'dhole_absent_pkg_xyz'",
                                  name="dhole_absent_pkg_xyz")

    xlsx = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-b-")) / "book.xlsx"
    xlsx.write_bytes(b"PK\x03\x04not really a workbook")
    monkeypatch.setattr(parse_mod, "_parse_xlsx", _raise)
    _content, err, _extras = parse_file_detailed(str(xlsx))
    assert "dhole_absent_pkg_xyz" in err, f"缺的那颗包没被点名：{err!r}"
    assert "cannot be imported" in err, err
    assert "pip install" in err, f"真缺包时不给装法：{err!r}"
    assert "docx" not in err, f"一句 .xlsx 的建议里冒出了 python-docx：{err!r}"


def test_the_package_name_is_read_out_of_the_message_when_name_is_empty(
        monkeypatch, tmp_path):
    """`e.name` 缺席、只有消息的那一发（`tests/test_install_hints.py` 的 import
    hook 就是这个形状）—— 包名仍要读得出来，装它的命令仍要在。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.split(".")[0] == "docx":
            raise ModuleNotFoundError("No module named 'docx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    target = tmp_path / "sample.docx"
    target.write_bytes(b"not a real docx")
    _content, err, _extras = parse_file_detailed(str(target))
    assert "docx" in err, err
    assert "pip install dhole-mcp[all]" in err, err
    assert "not a missing dependency" not in err, err


def test_an_import_inside_an_installed_package_is_not_called_a_missing_dependency(
        monkeypatch, txt_file):
    """第三种形状：包在盘上，可它里面的 import 解不开（版本不匹配）。

    旧代码在这里交出的正是那两句自话 —— 「Missing dependency」+「python-docx is
    installed - the parse failure was not a missing dependency」。
    """
    def _raise(_path):
        raise ImportError("cannot import name 'Document' from 'docx' (D:\\x\\docx\\__init__.py)",
                          name="docx")

    docx_file = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-c-")) / "a.docx"
    docx_file.write_bytes(b"PK\x03\x04not really a document")
    monkeypatch.setattr(parse_mod, "_parse_docx", _raise)
    _content, err, _extras = parse_file_detailed(str(docx_file))
    assert "docx" in err, err
    assert "Missing dependency" not in err, err
    assert "not a missing dependency" not in err, err
    assert "pip install" not in err, f"盘上就有的东西不许叫人去装：{err!r}"


def test_an_import_failure_that_names_no_module_claims_no_package(monkeypatch, txt_file):
    """`e.name` 是空的时候，这句话就没有资格点任何一颗包的名。"""
    def _raise(_path):
        raise ImportError("a bare import failure with no name at all")

    xlsx = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-d-")) / "book.xlsx"
    xlsx.write_bytes(b"PK\x03\x04not really a workbook")
    monkeypatch.setattr(parse_mod, "_parse_xlsx", _raise)
    _content, err, _extras = parse_file_detailed(str(xlsx))
    assert "Missing dependency" not in err, err
    assert "pip install" not in err, err
    assert "names no module" in err, err


def test_the_name_is_read_from_the_exception_itself():
    """`_failed_import_name` 只搬异常自己给出的名字：`e.name` 优先，其次消息里的
    "No module named 'x'"，两头都没有就是 ""。"""
    from dhole_mcp.parse import _failed_import_name

    assert _failed_import_name(
        ModuleNotFoundError("No module named 'a.b'", name="a.b")) == "a.b"
    assert _failed_import_name(
        ModuleNotFoundError("No module named 'docx'")) == "docx"
    assert _failed_import_name(ImportError(
        "cannot import name 'Document' from 'docx' (D:\\x\\docx\\__init__.py)",
        name="docx")) == "docx"
    # 只有消息、没有 e.name 的那一发（真实 Python 会带上 name，量具造的 hook 不会）：
    # 两句消息里都写着模块名，读得出来就不许退回"点不出包名"那一支。
    assert _failed_import_name(ImportError(
        "cannot import name 'Document' from 'docx' (D:\\x\\docx\\__init__.py)")) == "docx"
    assert _failed_import_name(ImportError(
        "cannot import name 'X' from 'dhole_mcp.charset' (p)")) == "dhole_mcp.charset"
    assert _failed_import_name(ImportError("nothing identifiable here")) == ""

