"""契约测试：旧版 Office 的 CFB 诊断、`.xlsm`/`.tsv`、本地 PDF 的 `pages=`/`password=`。

三件事一次做完，共同点是同一个承诺 —— **同一份文件，从磁盘读与从网上读必须给出同一个答案**。

* **旧版 Office 静默走错路。** `.doc` / `.xls` / `.ppt` 是 OLE2/CFB 容器，不是 zip。
  此前 `.doc` 被回一句「rename it to .txt」——改扩展名不改字节，照着做只会再错一次；
  而一个被改名成 `.docx` 的 CFB 文件（最常见的自救尝试）直接掉进 python-docx，回的是
  `PackageNotFoundError: Package not found at '...'`，一个既不说真格式、也不给出路的内建
  异常。两条都实测过。字节先于扩展名决定：魔数命中就按它命名，并给出转换命令。
* **`.xlsm` / `.tsv` 被挡在门外。** openpyxl 直接就能读宏表，`_sniff_delimiter` 本来
  就认 `\\t`；缺的只是放行。放行是零成本，拦截才是在替调用方做错决定。
* **加密 PDF 的报错在骗人。** `encrypted_pdf` 那条教调用方「pass the correct one via
  the 'password' option」，而 `doc_parse` **没有**这颗参数 —— 照着做就是
  `TypeError: got an unexpected keyword argument 'password'`（经 server 实测）。同一份
  文件经 `web_read` 有 `pages=` / `password=`，从磁盘读没有，正是 `_parse_pdf` docstring
  承诺不会发生的那类分裂。40 页的本地 PDF 还会整份回来外加一份 40 条 `table_of_contents`
  而调用方无从下手。

断言全部取自**本次运行真的解析出的东西**（字符数、正文、错误原文），不是从上限推的。
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib

import pytest

from dhole_mcp import params
from dhole_mcp import server as server_mod
from dhole_mcp.parse import SUPPORTED_EXTENSIONS, parse_file

#: The OLE2 / Compound File Binary signature, then filler. `_sniff_cfb` reads
#: exactly the first eight bytes, so nothing after them has to be a valid CFB.
_CFB_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512

#: A real, text-bearing PDF that ships with the suite. Re-paging or encrypting
#: it is cheaper — and more honest — than fabricating one with a PDF writer.
_BASE_PDF = pathlib.Path(__file__).resolve().parent / "background_checks.pdf"


def _cfb_file(tmp_path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(_CFB_HEADER)
    return str(path)


def _repage_pdf(tmp_path, name: str, times: int) -> str:
    """A real PDF whose page 1 is repeated `times` times — text on every page."""
    pypdf = pytest.importorskip("pypdf", reason="the [all] extra provides pypdf")
    reader = pypdf.PdfReader(str(_BASE_PDF))
    writer = pypdf.PdfWriter()
    for _ in range(times):
        writer.add_page(reader.pages[0])
    path = tmp_path / name
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def _encrypted_pdf(tmp_path, name: str, password: str) -> str:
    pypdf = pytest.importorskip("pypdf", reason="the [all] extra provides pypdf")
    reader = pypdf.PdfReader(str(_BASE_PDF))
    writer = pypdf.PdfWriter()
    writer.add_page(reader.pages[0])
    writer.encrypt(password)
    path = tmp_path / name
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def _call(name: str, args: dict) -> dict:
    """The dispatcher's own two-step: validate, then coerce types."""
    return params._coerce_arg_types(name, params._validate_tool_args(name, args))


class TestAnOle2FileIsNamedForWhatItIs:
    """CFB 容器按魔数命名，并给一条真能走通的路。"""

    @pytest.mark.parametrize("name,phrase,target", [
        ("old.doc", "Word 97-2003 (.doc)", "docx"),
        ("old.xls", "Excel 97-2003 (.xls)", "xlsx"),
        ("old.ppt", "PowerPoint 97-2003 (.ppt)", "pptx"),
    ])
    def test_each_legacy_family_names_itself_and_a_converter(
            self, tmp_path, name, phrase, target):
        text, err = parse_file(_cfb_file(tmp_path, name))

        assert text == "", "没有文本层，就不该伪造正文"
        assert phrase in err, err
        assert "OLE2/CFB" in err, err
        assert f"soffice --headless --convert-to {target}" in err, err
        # 旧行为：一句"改名成 .txt"。改扩展名不改字节，那条建议走不通。
        assert "rename it to .txt" not in err

    def test_a_cfb_file_renamed_to_docx_is_not_reported_as_a_missing_package(
            self, tmp_path):
        """最常见的自救尝试：把 .doc 改名成 .docx。

        旧行为是把它交给 python-docx，回一句 `PackageNotFoundError: Package not
        found at '...'` —— 不说真格式，也不说下一步。
        """
        text, err = parse_file(_cfb_file(tmp_path, "renamed.docx"))

        assert text == ""
        assert "PackageNotFoundError" not in err, "内建异常名不该是给调用方的答案"
        assert "named .docx but is a pre-2007 Word binary" in err, err

    def test_a_non_office_cfb_container_gets_the_generic_line(self, tmp_path):
        """`.msi` 也是 CFB，但它不是 Office 文档，不能被教"另存为 .docx"。"""
        _, err = parse_file(_cfb_file(tmp_path, "installer.msi"))

        assert "another CFB container (msi, msg, ...)" in err, err

    def test_a_real_zip_docx_is_not_caught_by_the_sniffer(self, tmp_path):
        """反向守卫：魔数检查只认 CFB，不许误伤一份正常的 zip 型 .docx。"""
        docx = pytest.importorskip("docx", reason="the [all] extra provides python-docx")
        doc = docx.Document()
        doc.add_paragraph("正文一句。")
        path = tmp_path / "real.docx"
        doc.save(str(path))

        text, err = parse_file(str(path))

        assert err == "", err
        assert "正文一句" in text


class TestTheNewExtensionsActuallyParse:
    """`.xlsm` / `.tsv` 不是被放行到一半再炸，而是真的读得出表。"""

    def test_a_macro_enabled_workbook_parses_like_a_plain_one(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl", reason="the [all] extra provides openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "宏表"
        ws.append(["产品", "数量"])
        ws.append(["甲", 3])
        path = tmp_path / "macro.xlsm"
        wb.save(str(path))

        text, err = parse_file(str(path))

        assert err == "", err
        assert "| 产品 | 数量 |" in text
        assert "| 甲 | 3 |" in text

    def test_a_tab_separated_file_parses_into_a_table(self, tmp_path):
        path = tmp_path / "data.tsv"
        path.write_text("区域\t营收\n华东\t1200\n华南\t980\n", encoding="utf-8")

        text, err = parse_file(str(path))

        assert err == "", err
        assert "| 区域 | 营收 |" in text
        assert "| 华东 | 1200 |" in text
        assert "\t" not in text, "分隔符应当被吃成表格，而不是留在正文里"

    def test_the_two_new_extensions_are_advertised_everywhere(self):
        """白名单、wire 属性描述、扩展名表三处同颗 —— 少一处就是"够不着"。"""
        assert ".xlsm" in SUPPORTED_EXTENSIONS
        assert ".tsv" in SUPPORTED_EXTENSIONS

        defs = {d["name"]: d for d in server_mod.MasterFetchServer._TOOL_DEFS}
        file_path = defs["doc_parse"]["inputSchema"]["properties"]["file_path"]
        desc = file_path.get("description", "")
        assert ".xlsm" in desc and ".tsv" in desc, desc


class TestALockedPdfCanBeOpened:
    """加密 PDF：正确的口令读得出，缺/错口令各自说清是哪一种。"""

    def test_the_right_password_returns_the_text(self, tmp_path):
        path = _encrypted_pdf(tmp_path, "locked.pdf", "s3cret")

        text, err = parse_file(str(path), password="s3cret")

        assert err == "", err
        assert len(text) > 1000, f"只读到 {len(text)} 字，口令路径没真的解出正文"

    def test_without_a_password_the_error_says_none_was_supplied(self, tmp_path):
        path = _encrypted_pdf(tmp_path, "locked.pdf", "s3cret")

        text, err = parse_file(str(path))

        assert text == ""
        assert "encrypted_pdf" in err
        assert "no password was supplied" in err, err

    def test_a_wrong_password_is_reported_as_rejected_not_as_corrupt(self, tmp_path):
        """错口令与"没给口令"是两件事：前者要说口令被拒，不能含糊成打不开。"""
        path = _encrypted_pdf(tmp_path, "locked.pdf", "s3cret")

        _, err = parse_file(str(path), password="not-it")

        assert "the supplied password was rejected" in err, err

    def test_the_password_the_error_asks_for_is_one_the_dispatcher_accepts(self):
        """这一条就是事故本身：报错教人传 `password`，而那颗参数原先不存在。

        四处必须同颗 —— 白名单、wire 属性、类型归一表、分发读法。少一处，
        「照报错说的做」就会变成 `TypeError: unexpected keyword argument`。
        """
        assert "password" in server_mod._TOP_LEVEL_ARGS["doc_parse"], "白名单没放行"
        assert "pages" in server_mod._TOP_LEVEL_ARGS["doc_parse"], "白名单没放行"

        defs = {d["name"]: d for d in server_mod.MasterFetchServer._TOOL_DEFS}
        props = defs["doc_parse"]["inputSchema"]["properties"]
        assert "password" in props and "pages" in props, "wire 上看不见这两颗参数"

        assert params._ARG_TYPES["doc_parse"]["password"] == "strnum"
        assert params._ARG_TYPES["doc_parse"]["pages"] == "strnum"

        source = inspect.getsource(server_mod.MasterFetchServer._dispatch)
        assert 'args.get("password"' in source, "分发器没有真的把 password 读出来"
        assert 'args.get("pages"' in source, "分发器没有真的把 pages 读出来"

        # 端到端：字符串客户端发来的两颗参数必须被读过去，而不是抛异常。
        coerced = _call("doc_parse",
                        {"file_path": "a.pdf", "pages": "1-2", "password": "x"})
        assert coerced["pages"] == "1-2" and coerced["password"] == "x"


class TestAPageRangeNarrowsALocalPdf:
    """本地 PDF 的 `pages=` 与 URL 路径同义：只收窄抽取量，不改变文件读取。"""

    def test_a_page_range_extracts_less_than_the_whole_file(self, tmp_path):
        path = _repage_pdf(tmp_path, "two.pdf", times=2)

        whole, err_all = parse_file(str(path))
        first, err_one = parse_file(str(path), pages="1")

        assert err_all == "" and err_one == "", (err_all, err_one)
        assert 0 < len(first) < len(whole), (
            f"pages='1' 收了 {len(first)} 字，整份 {len(whole)} 字 —— 范围没起作用")

    def test_page_one_is_about_half_of_a_two_page_document(self, tmp_path):
        """页 1 重复两次造出 2 页，所以"只取第 1 页"应当接近整份的一半。"""
        path = _repage_pdf(tmp_path, "two.pdf", times=2)

        whole, _ = parse_file(str(path))
        first, _ = parse_file(str(path), pages="1")

        ratio = len(first) / len(whole)
        assert 0.4 < ratio < 0.6, f"比例 {ratio:.3f} 偏离半份，范围可能被忽略或重复计"

    def test_the_range_reaches_the_server_layer_too(self, tmp_path):
        """走一遍 server 的 `doc_parse`，证明那颗参数端到端可达。"""
        path = _repage_pdf(tmp_path, "two.pdf", times=2)

        whole = asyncio.run(server_mod.MasterFetchServer().doc_parse(str(path)))
        first = asyncio.run(
            server_mod.MasterFetchServer().doc_parse(str(path), pages="1"))

        whole_chars = sum(len(c) for c in (whole.content or []))
        first_chars = sum(len(c) for c in (first.content or []))
        assert 0 < first_chars < whole_chars, (first_chars, whole_chars)
