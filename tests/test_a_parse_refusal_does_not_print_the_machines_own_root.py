"""本地文档的拒绝不许把这台的根印在回执里：判决说什么，路径不是它的话。

现场（2026-10-15 · P4 复验 R02-F15 的邻域那一发时量到 · R02-044 · P3 · 单条）：

    doc_parse(file_path=<一份假 .docx>) 走 9202 真 CLI
      → error=「Parse error (.docx): PackageNotFoundError: Package not found at
        'D:\\tools\\dhole-mcp\\review\\.runtime\\p4\\f15-sample.docx'」

同一封响应的 `url` 那一格自己是「file:///<server cwd>/review/…」—— 遮蔽过的。也就是说
不是"整体不遮蔽"，是**漏了一处**：17.0 的 `paths.mask_machine_paths`（2026-10-09 审计 D11）
挂在 `File not found:` 那一句上，而异常那一支把解析库的原始消息整条交出，库的消息里
带着它收到的绝对路径。对宿主型客户端（cwd 就是 host 的安装目录），这一行印出的是
装 dhole 的那台机器，不是用户自己打的那个名字。

契约（本文件钉住）：
  * 异常两支（`except ImportError` / `except Exception`）交出的 error 不许带本机的根
    （cwd 与 home 两个都是），但**要说得出这一发知道的**：异常类名、扩展名、库自己那句
    的其余部分；
  * `File not found:` 那一支照旧遮蔽（反面锁：已经修好的那一处不许被这一刀弄回原形）；
  * 遮蔽不许把判决吃掉：`content` 仍空、`error` 仍非空、`Unsupported file type` 那一句
    一个字符都不动；
  * 遮蔽只有一位作者（`paths.mask_machine_paths`），parse 这边不许再写一条正则去删路径。
"""

from __future__ import annotations

import os
import pathlib

from dhole_mcp import paths as paths_mod
from dhole_mcp.parse import parse_file_detailed

ROOTS = [p for p in (os.path.abspath(os.getcwd()), os.path.expanduser("~")) if p]


def _roots_leaked(text: str) -> list[str]:
    return [r for r in ROOTS if r and r.lower() in (text or "").lower()]


def test_a_package_error_does_not_print_the_machines_root(tmp_path):
    bogus = tmp_path / "sample.docx"
    bogus.write_bytes(b"PK\x03\x04not a real document at all")
    _content, err, _extras = parse_file_detailed(str(bogus))
    assert err, "拒绝本身没了"
    assert not _roots_leaked(err), f"本机根被印进回执：{err!r}"
    # 遮蔽之后这句话还得说得出这一发知道的东西
    assert "PackageNotFoundError" in err, err
    assert ".docx" in err, err


def test_an_import_error_does_not_print_the_machines_root(monkeypatch, tmp_path):
    from dhole_mcp import parse as parse_mod

    def _raise(_path):
        # 库的消息照例把**它翻过的那条目录**写全（根后面还带一段），
        # 这正是遮蔽能吃住的形状。"根出现在句尾、后面没有分隔符"那一格遮蔽今天吃不住，
        # 那是另一条账（R02-045），不在这一刀里悄悄改共享的遮蔽器。
        raise ModuleNotFoundError(
            f"No module named 'dhole_absent_pkg' (searched under "
            f"{os.path.join(os.getcwd(), 'site-packages', 'dhole_absent_pkg')})")

    monkeypatch.setattr(parse_mod, "_parse_xlsx", _raise)
    xlsx = tmp_path / "book.xlsx"
    xlsx.write_bytes(b"PK\x03\x04not a real workbook")
    _content, err, _extras = parse_file_detailed(str(xlsx))
    assert not _roots_leaked(err), f"缺依赖那一句里带着这台的路径：{err!r}"
    assert "dhole_absent_pkg" in err, err


def test_the_missing_file_branch_keeps_its_mask(tmp_path):
    """反面锁：D11 那一处已经修好的不许被这一刀弄回原形。"""
    gone = pathlib.Path(os.path.expanduser("~")) / "definitely-not-here-42.docx"
    _content, err, _extras = parse_file_detailed(str(gone))
    assert "File not found" in err, err
    assert not _roots_leaked(err), err


def test_the_unsupported_extension_sentence_is_untouched(tmp_path):
    rar = tmp_path / "archive.rar"
    rar.write_bytes(b"RAR6\x00not an archive dhole opens")
    _content, err, _extras = parse_file_detailed(str(rar))
    assert err.startswith("Unsupported file type '.rar'"), err
    assert "rename it to .txt" in err, err


def test_the_mask_has_one_author():
    """parse 不许自己删路径：它交出的句子必须与 `paths.mask_machine_paths` 逐字同。

    比对而不是数正则 —— 一份复制品能过"没有第二条正则"的字面锁，过不了同输入同输出。
    """
    import inspect

    from dhole_mcp import parse as parse_mod

    local = os.path.join(os.getcwd(), "review", "x.docx")
    for sample in (
        f"Parse error (.docx): PackageNotFoundError: Package not found at '{local}'",
        f"searched under {os.path.join(os.path.expanduser('~'), '.dhole', 'cache')}",
        "Unsupported file type '.rar'. Supported: .csv, .docx",   # 没有根的那一句照旧原样
    ):
        assert parse_mod._parse_refusal(sample) == paths_mod.mask_machine_paths(sample), sample

    src = inspect.getsource(parse_mod)
    assert src.count("_parse_refusal(") >= 6, "五支异常那一句都必经这位遮蔽作者"
    assert 're.compile(r"[A-Za-z]:' not in src, "parse 自己写了一条删路径的正则 = 第二个作者"
