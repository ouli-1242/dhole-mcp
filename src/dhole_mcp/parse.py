"""Local file parsing for Dhole MCP.

Converts local files to Markdown so agents can read documents without a web
fetch. Part of the [all] extra (python-docx, openpyxl). Graceful degradation: if
deps are missing, returns a clear error.

Supported formats:
- .html / .htm / .xhtml → trafilatura + markdownify (reuses existing extraction)
- .docx         → python-docx: headings, paragraphs and tables in DOCUMENT
                  order; what a text reader cannot carry (headers/footers,
                  footnotes, comments, images) is named in the first line, the
                  same contract .pptx/.odt already follow
- .xlsx / .xlsm → openpyxl: sheets → Markdown tables (macro-enabled .xlsm is
                  the same OOXML zip)
- .csv / .tsv  → stdlib csv: → Markdown table (the delimiter is sniffed)
- .pdf          → pdf_extractor (same pipeline web_read uses for PDF URLs:
                  layout-aware markdown, OCR fallback for scans, quality score,
                  and the same pages= / password= knobs)
- .md / .markdown / .txt → as-is, through the same charset detection .csv uses
- .json / .yaml / .yml   → as-is, but validated (an unparseable file is an error)
- .pptx / .odt  → stdlib zip + XML, no python-pptx / odfpy: the text of each
                  slide / each paragraph, with what a text reader cannot carry
                  named in the first line of the output. Slides come out in the
                  deck's own PLAY order (presentation.xml's p:sldIdLst), not in
                  filename order — the number in slideN.xml is a filename, not
                  a position; speaker notes come out under the slide they belong
                  to, marked `> Notes:`. `<a:fld>` layout fields (slide number,
                  date) are not text and are dropped

Pre-2007 Office files (.doc / .xls / .ppt) are OLE2/CFB, not zips: they are
refused by MAGIC BYTES rather than by extension, so a `.doc` renamed to `.docx`
is named for what it really is instead of falling through to python-docx's
PackageNotFoundError. The refusal says how to convert one.

Text formats used to be refused with "read it with your own file tool", which
presupposes a caller that has one. A client that only speaks MCP reaches a local
document through this tool or not at all, so the refusal was a rule for us rather
than a service to the caller.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import posixpath
import re
import threading
from collections import OrderedDict

logger = logging.getLogger("dhole_mcp.parse")

# Supported extensions. `.xlsm` (macro-enabled OOXML) and `.tsv` are here because
# they cost nothing and were only ever missing from this list: the first is the
# same zip openpyxl already opens, the second is the same csv reader whose
# delimiter sniffer already lists "\t" among its candidates.
SUPPORTED_EXTENSIONS = {".html", ".htm", ".xhtml",
                        ".docx", ".xlsx", ".xlsm", ".csv", ".tsv",
                        ".pdf", ".pptx", ".odt",
                        ".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}
# Formats that need no conversion at all. They used to be refused with "read the
# file yourself", which assumed the caller HAS a file tool: a client that only
# speaks MCP has exactly one way to reach a local document, and it is this one.
# Refusing it there was a rule for ourselves, not for the user.
TEXTUAL_EXTENSIONS = {".md", ".markdown", ".txt"}
# Text the caller may want validated rather than just read.
STRUCTURED_TEXT_EXTENSIONS = {".json", ".yaml", ".yml"}


# Maximum file size for parsing (50 MB) to prevent OOM on huge files
MAX_PARSE_FILE_SIZE = 50 * 1024 * 1024


#: The OLE2 / Compound File Binary signature. Word 97-2003 `.doc`, Excel `.xls`
#: and PowerPoint `.ppt` all begin with these eight bytes, as does anything else
#: built on CFB (msi, msg, ...). A modern .docx/.xlsx/.pptx is a ZIP ("PK"), so
#: this magic never appears on a file whose extension tells the truth - which is
#: why the check runs before the extension decides anything.
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: CFB-shaped names this reader could plausibly be handed -> (the sentence that
#: names what the file really is, the modern format to convert to). Anything
#: else that sniffs as CFB gets the generic line, because an `.msi` is not an
#: Office document and must not be told to "Save As .docx".
_CFB_LEGACY = {
    ".doc": ("This file is Word 97-2003 (.doc) - OLE2/CFB, not the zip-based "
             ".docx this reader opens", "docx"),
    ".docx": ("This file is named .docx but is a pre-2007 Word binary - "
              "OLE2/CFB, not the zip this reader opens", "docx"),
    ".xls": ("This file is Excel 97-2003 (.xls) - OLE2/CFB, not the zip-based "
             ".xlsx this reader opens", "xlsx"),
    ".xlsx": ("This file is named .xlsx but is a pre-2007 Excel binary - "
              "OLE2/CFB, not the zip this reader opens", "xlsx"),
    ".xlsm": ("This file is named .xlsm but is a pre-2007 Excel binary - "
              "OLE2/CFB, not the zip this reader opens", "xlsx"),
    ".ppt": ("This file is PowerPoint 97-2003 (.ppt) - OLE2/CFB, not the "
             "zip-based .pptx this reader opens", "pptx"),
    ".pptx": ("This file is named .pptx but is a pre-2007 PowerPoint binary - "
              "OLE2/CFB, not the zip this reader opens", "pptx"),
}


def _sniff_cfb(file_path: str) -> bool:
    """True when the file begins with the OLE2/CFB signature. Never raises."""
    try:
        with open(file_path, "rb") as fh:
            return fh.read(len(_CFB_MAGIC)) == _CFB_MAGIC
    except OSError:
        return False


#: 未支持扩展名那一族的下一步，由点名支持集的人自己写（评审 R02-022）。同一串字进
#: `error` 也进 `next_action`；兜底句从前替这一族转述过一次，转述就是第二个作者。
_UNTEXTED_EXT_WAY_FORWARD = ("If the file is plain text under another extension, "
                             "copy or rename it to .txt (or .md) and parse that.")

#: 大小超限那一族的下一步（评审 R02-124 · r241 现场：这一族从前没有作者，于是落进
#: `quality` 那句「the file was found and read」的兜底 —— 而 `os.path.getsize` 之后
#: 一个字节都没读过，那句是假话）。帽顶 `MAX_PARSE_FILE_SIZE` 是一颗模块常量，没有
#: 任何环境变量或参数能抬它，所以「这是门不是旋钮」这句在这里是真的。
_OVERSIZE_WAY_FORWARD = (
    "no byte of this file was read - the limit is on the file's total size, which "
    "`error` names, so `pages=` and `offset=` cannot change the answer. Split it, or "
    "copy the part you need into a smaller file and parse that; this is a guard, not "
    "a knob: no argument raises it."
)

#: 交回给门的那个事实：这一支在真去读内容之前就把这一发拒了。它是**形状**，不是句子 ——
#: 句子（「这一发什么也没发生」那半句的动词）由 `context.nothing_went_out()` 按门装配，
#: 这里手抄一份就是第二位作者（评审 R02-089/F83 那条通则）。
BODY_UNREAD = "body_unread"


def _cfb_way_forward(target: str) -> str:
    """CFB 那一族唯一的下一步，由写拒绝的人自己交（评审 R02-022）。

    同一串字既进 `error`（读判决的人在那里看）也进 `next_action`（读下一步的人在那里看）——
    一处作者、两处搬运，不是两个人各说一遍。前一是 `quality` 的兜底句替**所有** parse 失败
    说「按 error 建议的那个改名去修」，而这条 error 明说"改名不改字节"：同一封响应里两句话
    互相否认，而假的那句才是调用方照着行动的。
    """
    return (f"Convert it first - `soffice --headless --convert-to {target} <file>` "
            f"(LibreOffice), or the authoring app's 'Save As .{target}' - then "
            f"parse the result.")


def _cfb_unknown_way_forward() -> str:
    """未知 CFB 容器的那一句：没有转换目标可点名。

    `_cfb_way_forward(target)` 教的是一条**文档**的路（LibreOffice 转格式、原作者应用
    另存）。对一颗 .msi / .msg 那不是出路而是零信息 —— error 自己已经说了"这不是本文
    读取器开的文档格式"，出路却把它当 Word 教它另存（评审 R02-043：R02-F12 把这句话从
    error 尾巴搬进 next_action 之后，它升格成唯一下一步，错得更显眼）。这一支不兜任何
    目标名：目标由 `_CFB_LEGACY` 那三对（加改名的两支）给，查不到就是没有。
    """
    return ("This container cannot be opened here, and there is no conversion "
            "target to name: an OLE2/CFB file that is not a legacy Office "
            "document is a package of streams (an installer, a mail message, a "
            "stored object), not text. Say what you need out of it and take it "
            "from the app that owns that format - parsing this file will not "
            "produce text.")


def _cfb_refusal(ext: str) -> tuple[str, str]:
    """The refusal for an OLE2/CFB file, named for what it actually is.

    Both shapes this replaces were worse. A `.doc` was answered with "rename it
    to .txt", which cannot work - renaming does not change the bytes - and a CFB
    file renamed to `.docx` (a very common attempt) fell through to python-docx
    and came back as ``Parse error (.docx): PackageNotFoundError: Package not
    found at '...'``, an internal exception naming neither the real format nor
    any way forward. Both measured.

    Returns ``(error, way_forward)``: the diagnosis and the one sentence telling
    the caller what to do, so the way forward never needs a second author.
    """
    entry = _CFB_LEGACY.get(ext)
    if entry:
        head, target = entry
        way = _cfb_way_forward(target)
    else:
        head = ("This file starts with the OLE2/CFB signature: a pre-2007 "
                "Office document, or another CFB container (msi, msg, ...) - "
                "not a document format this reader opens")
        way = _cfb_unknown_way_forward()
    return (f"{head}. There is no text layer a reader can walk here, and "
            f"renaming it does not change the bytes. {way}"), way


#: Rows a sheet may render, header included. Reading stops here on purpose (a
#: 100k-row sheet would blow the caller's context, not help it) — but what the
#: cap left behind is REPORTED. The note used to read "99 more rows shown" while
#: those 99 rows were exactly the ones being dropped, on a sheet that had 400
#: more: a truncation that reads as completeness is the worst outcome available.
_XLSX_MAX_ROWS = 101


# ─── Byte-level decoding ──────────────────────────────────────────────────────
#
# A local file carries no HTTP header to declare its charset, and .csv/.html
# used to be read as UTF-8 with errors="replace". Measured live through the MCP
# server: a 90-byte GBK CSV - what Excel's "CSV (逗号分隔)" writes on a Chinese
# Windows box - parsed to "| ���� | ���� |" and still reported content_ok=true,
# error="". Mojibake that looks like success is the worst outcome available.
#
# The candidate order and the damage score live in dhole_mcp.charset, next to
# the HTTP path's version of the same decision, so the two cannot drift.

_HTML_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


def _html_declared_charset(body: bytes) -> str:
    """The charset the document names for itself, or '' when it names none.

    Only the head is scanned: browsers require <meta charset> within the first
    1024 bytes for it to count, and scanning the whole file risks matching prose
    that happens to contain "charset=". A declaration that contradicts the bytes
    is harmless - charset.decode_file_bytes falls through to detection when the
    declared name fails to decode strictly.
    """
    match = _HTML_CHARSET_RE.search(body[:2048])
    return match.group(1).decode("ascii", errors="ignore") if match else ""


def _decode_body(body: bytes, declared: str = "", asked: "str | None" = None
                 ) -> tuple[str, dict]:
    """Decode file bytes and package the envelope fields. See charset.decode_file_bytes.

    `asked` 是**调用方自己点名的那一颗**（`encoding=`），与 `declared` 分开：后者还包含
    文件里 meta 自己说的字符集，而 HTML 那条路上两个来源会撞在一起。谁点的名要单独说得
    出来，否则「你点的 shift_jis」与「这份 html 写的 utf-8」在回执里长成一个样子。
    None（默认）= 这一条路上 declared 就是调用方的名字。
    """
    from dhole_mcp.charset import (contested_rival, decode_file_bytes,
                                   encoding_basis)

    text, used, damage = decode_file_bytes(body, declared)
    extras: dict = {"encoding": used}
    if damage:
        extras["decode_damage"] = damage
    basis = encoding_basis(body, declared, used)
    if basis == "contested":
        # 那句话要点名**两个**名字，而竞争者的名字只有这一层量得出来。让 quality 自己去
        # 猜"另一个是谁"就是给同一件事添第二个作者。
        extras["encoding_rival"] = contested_rival(body, used)
    if basis in ("guessed", "fallback", "contested"):
        # 「这一份是按谁读的」——只有需要说的时候才写。guessed 那一支是 gb18030 严格解得开
        # Shift_JIS / EUC-KR 的字节（charset.py 记着这条实测边界），出来的是**看着像中文的
        # 错字**、damage 0、content_ok true；fallback 那一支是调用点了名而那颗名字解不开、
        # 由检测顶上（R02-021）；contested 那一支是点名的那颗**解得开**、正文也照它交，
        # 但这批字节还有第二种干净的读法而文本不同（R02-037：GBK 件点名 big5，三把现成的
        # 尺子全读 0/False）。两支都由 quality._agent_hints 说那句话，都不动判词。
        extras["encoding_basis"] = basis
    name_asked = declared if asked is None else asked
    if (name_asked or "").strip():
        extras["encoding_asked"] = name_asked.strip()
    return text, extras


def _read_text_file(file_path: str, declared: str = "") -> tuple[str, dict]:
    """Read a local file as bytes and decode it. See charset.decode_file_bytes."""
    with open(file_path, "rb") as f:
        body = f.read()
    return _decode_body(body, declared)


_NO_MODULE_RE = re.compile(r"No module named '([^']+)'")


def _parse_refusal(err: str) -> str:
    """两支异常交出的句子都过同一位遮蔽作者（评审 R02-044）。

    解析库的消息里带着它收到的那个绝对路径（`PackageNotFoundError: Package not found at
    'D:\\tools\\dhole-mcp\\…'`），而那条路径属于**装了 dhole 的这台机器** —— 宿主型客户端的
    cwd 就是 host 的安装目录，用户从没提过它。同一封响应的 `url` 那一格早就写成
    `file:///<server cwd>/…`，`File not found:` 那一句 17.0 也过了遮蔽（审计 D11）；
    漏的是异常那两支。判决一个字不少：类名、扩展名、库那句的其余部分照旧在。
    """
    from dhole_mcp import paths as _paths
    return _paths.mask_machine_paths(err)


_CANNOT_IMPORT_RE = re.compile(r"cannot import name '[^']+' from '([^']+)'")


def _failed_import_name(exc: BaseException) -> str:
    """这一发 ImportError blame 的是哪一颗模块 —— 由异常自己回答，不是猜。

    `e.name` 是导入机制留下的名字（`from .charset import X` → 'dhole_mcp.charset'，
    `import absent_pkg` → 'absent_pkg'）；手工抛出的那颗（测试里的 import hook）只有
    消息，所以名字从消息里读 —— Python 的两句消息都带着模块名（"No module named 'x'"
    与 "cannot import name 'Y' from 'x'"）。三处都给不出时返回 "" —— 那一刻这句响应
    就没有资格点任何一颗包的名（评审 R02-040）。
    """
    named = getattr(exc, "name", "") or ""
    if named:
        return named
    text = str(exc)
    for pattern in (_NO_MODULE_RE, _CANNOT_IMPORT_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def parse_file(file_path: str, encoding: str = "", pages: str | None = None,
               password: str | None = None) -> tuple[str, str]:
    """Parse a local file to Markdown.

    Returns (content: str, error: str). On success error is empty.
    On failure content is empty and error explains what went wrong.
    Never raises.

    ``pages`` / ``password`` apply to .pdf only; see parse_file_detailed.
    """
    content, error, _extras = parse_file_detailed(file_path, encoding, pages, password)
    return content, error


def parse_file_detailed(file_path: str, encoding: str = "",
                        pages: str | None = None,
                        password: str | None = None) -> tuple[str, str, dict]:
    """Parse a local file to Markdown, plus the envelope fields it can fill.

    Returns (content, error, extras). For PDFs ``extras`` carries
    table_of_contents / metadata / quality_score / content_ok - a local PDF
    should report the same envelope as one fetched by URL, and used to report
    toc=[] and metadata={} while the URL path reported both. For .html/.csv it
    carries ``encoding`` (the charset the bytes were decoded as) and, when the
    text came out damaged, ``decode_damage``. Empty for the other formats.
    Never raises.

    ``encoding`` overrides auto-detection; leave it empty unless the caller
    knows better than the bytes do. ``pages`` / ``password`` are .pdf only and
    carry web_read's meaning; every other format says so in one declared line
    instead of answering as if nothing had been asked.
    """
    content, error, extras = _parse_one_file(file_path, encoding, pages, password)
    if error or not content:
        return content, error, extras
    ext = os.path.splitext(os.path.expanduser(file_path))[1].lower()
    if ext == ".pdf":
        # 这一条腿上两个参数都真的作用到了（`_parse_pdf` 把它们交给抽取器），
        # 于是没有东西要申报 —— 报"没作用"反而是假话。
        return content, error, extras
    lines = _inert_parameter_lines(ext, pages, password)
    if not lines:
        return content, error, extras
    return _insert_declaration_after_first_line(content, lines), error, extras


def _inert_parameter_lines(ext: str, pages: str | None,
                           password: str | None) -> list[str]:
    """一颗被收下、却没有作用到这份文件身上的参数，自己说一声。

    `pages` / `password` 只在 .pdf 那条腿上被读（`_parse_pdf`）。实测 2026-10-16
    （9202 · 本地文件）：`doc_parse(week1-导读.docx, pages="1-2")` 交回 384 字符，与不带
    `pages` 逐字节相同，整封回执里连 "pages" 这个字都没出现 ——
    调用方读到的是"这文档就这么长"，而她自己要求的是两页。沉默在这里不是简洁，是把
    一次没发生的过滤伪装成一次完整的交付。

    两条各说各的理由（一个是"没有页可切"，一个是"不需要密码"），`password` 的**值
    永不回显** —— 参数名就是它的全部身份。正文一寸不动：这一行是加在交付上的申报，
    不是内容。
    """
    lines: list[str] = []
    if pages:
        lines.append(
            f'> pages="{pages}" did nothing here: {ext or "this file"} has no page '
            "concept (pages= slices .pdf only). What is below is the whole file, "
            "not a page range.")
    if password:
        lines.append(
            f"> password= did nothing here: {ext or 'this file'} is not opened with a "
            "password (password= is read for an encrypted .pdf only). What is below is "
            "the whole file.")
    return lines


def _insert_declaration_after_first_line(content: str, lines: list[str]) -> str:
    """格式自己的首行（`[Word document; …]`）继续领第一行，申报紧跟其后。"""
    head, sep, rest = content.partition("\n")
    if head.startswith("["):
        return head + "\n" + "\n".join(lines) + sep + rest
    return "\n".join(lines) + "\n\n" + content


def _parse_one_file(file_path: str, encoding: str = "",
                    pages: str | None = None,
                    password: str | None = None) -> tuple[str, str, dict]:
    if not file_path:
        return "", "file_path is required", {}

    # Expand ~ and resolve to absolute
    file_path = os.path.expanduser(file_path)
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(file_path)

    if not os.path.exists(file_path):
        # 同一句话在这条路上还有第二个作者（server.doc_parse 那句），两处都要遮蔽：
        # 走到这里说明文件在解析那一刻不在了，而 file_path 已经是绝对形式，里面
        # 通常带着本机的根。（2026-10-09 审计 D11；见 paths.mask_machine_paths。）
        from dhole_mcp import paths as _paths
        return "", _paths.mask_machine_paths(f"File not found: {file_path}"), {}

    # Guard against OOM: reject files larger than 50 MB
    file_size = os.path.getsize(file_path)
    if file_size > MAX_PARSE_FILE_SIZE:
        # 这一支此后一个字节都不读：它量的是大小。所以它既要交出下一步（那位作者就是
        # 写这条 error 的人），也要交出「没读过正文」这个**事实**给门去装配申报
        # （评审 R02-124 · r241 数到的两颗假脸之一）。
        return "", (
            f"File too large ({file_size / 1024 / 1024:.1f} MB). "
            f"Maximum supported size is {MAX_PARSE_FILE_SIZE // 1024 // 1024} MB."
        ), {"way_forward": _OVERSIZE_WAY_FORWARD, BODY_UNREAD: True}

    ext = os.path.splitext(file_path)[1].lower()

    # The bytes decide before the extension does. A CFB file is never a modern
    # Office document, so one check covers both a plain `.doc` and a `.docx`
    # that is really a renamed `.doc` - the two shapes that used to get a
    # useless answer (a rename hint that cannot work, and python-docx's
    # PackageNotFoundError).
    if _sniff_cfb(file_path):
        _err, _way = _cfb_refusal(ext)
        return "", _err, ({"way_forward": _way} if _way else {})

    if ext not in SUPPORTED_EXTENSIONS:
        # 报错点名支持集，再给一条下一步。只写「不支持」的话，agent 会以为这条路
        # 整个走不通而放弃读文件——而它手里那份 .log/.rst/.ini 多半就是文本。
        #
        # 这一族和上面那颗超限的是同一件事的两面（评审 R02-124）：扩展名不认识 ⇒ 连
        # 「要不要按这个格式解」都没问到，正文一个字节没读过。所以它也交出 BODY_UNREAD。
        return "", (
            f"Unsupported file type '{ext}'. Supported: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}. "
            f"{_UNTEXTED_EXT_WAY_FORWARD}"
        ), {"way_forward": _UNTEXTED_EXT_WAY_FORWARD, BODY_UNREAD: True}

    try:
        if ext in (".html", ".htm", ".xhtml"):
            content, extras = _parse_html(file_path, encoding)
            return content, "", extras
        elif ext == ".docx":
            return _parse_docx(file_path), "", {}
        elif ext in (".xlsx", ".xlsm"):
            return _parse_xlsx(file_path), "", {}
        elif ext in (".csv", ".tsv"):
            content, extras = _parse_csv(file_path, encoding)
            return content, "", extras
        elif ext == ".pdf":
            return _parse_pdf(file_path, pages=pages, password=password)
        elif ext in TEXTUAL_EXTENSIONS:
            text, extras = _read_text_file(file_path, encoding)
            return text, "", extras
        elif ext in STRUCTURED_TEXT_EXTENSIONS:
            return _parse_structured_text(file_path, ext, encoding)
        elif ext == ".pptx":
            return _parse_pptx(file_path), "", {}
        elif ext == ".odt":
            return _parse_odt(file_path), "", {}
    except ImportError as e:
        # 一颗 None 不许有三种读法，一句 ImportError 也一样（评审 R02-040）。这个
        # `except` 罩着整个分派块，块里既有第三方 import（docx / openpyxl / pdf），也有
        # dhole 自己的 `_read_text_file` / `_parse_csv` 与从 `.charset` 的取符号。从前
        # 全都落进同一句「Missing dependency for {ext} parsing」，紧跟其后的
        # `docx_install_advice()` 又自己否认（"python-docx is installed - the parse
        # failure was not a missing dependency"）—— 一封响应两句话互相否认，而
        # `content_ok=false` 把调用方支去装一颗根本不缺的包；那份建议还固定点名 docx，
        # 于是一份 .xlsx 的失败听人说 python-docx。
        # 谁缺东西这件事，Python 自己已经分了名字，不需要再探一次环境（再探一次还会被
        # import hook 之类的中间状态骗过去 —— 实测）：类型名就是这一发的根据。四种形状
        # 各量过一次：
        #   from .charset import X   → ImportError, name='dhole_mcp.charset' → dhole 自己的失败
        #   import some_absent_pkg   → ModuleNotFoundError, name='some_absent_pkg' → 真缺，点名+给装法
        #   cannot import name Y from docx → ImportError, name='docx' → 包在盘上，是它里面的符号对不上
        #   ImportError("…") 无 name 也无包名 → 这一发不知道谁缺东西，于是谁的名都不点
        # 判决位一支都不动：失败仍记失败（error 非空、content 空），本刀改的是那句话。
        failed = _failed_import_name(e)
        failed_top = failed.split(".")[0]
        if failed_top == "dhole_mcp":
            return "", _parse_refusal(
                f"Parse error ({ext}): dhole's own import failed - {e}. Nothing is "
                "missing on your side, so there is nothing to add to the "
                "environment: this is a bug in this build (or a process holding a "
                "half-loaded module set). Restarting the server is the only action "
                "that can change the outcome."
            ), {}
        from dhole_mcp import install as _install
        if failed and isinstance(e, ModuleNotFoundError):
            return "", _parse_refusal(
                f"Missing dependency for {ext} parsing: `{failed}` cannot be "
                f"imported here - {e}. Install it with: "
                f"{_install.full_install_command()}"
            ), {}
        if failed:
            return "", _parse_refusal(
                f"Parse error ({ext}): the `{failed_top}` package is on disk, but "
                f"what dhole imports out of it is not there - {e}. Nothing is "
                "missing from the environment, so this is a version mismatch inside "
                f"that package; `{failed_top}` is what to look at, not dhole's "
                "install."
            ), {}
        return "", _parse_refusal(
            f"Parse error ({ext}): {type(e).__name__}: {str(e)[:200]}. The failing "
            "import names no module, so this response cannot say which package, "
            "if any, is missing."
        ), {}
    except Exception as e:
        return "", _parse_refusal(
            f"Parse error ({ext}): {type(e).__name__}: {str(e)[:200]}"), {}

    return "", f"Unsupported format: {ext}", {}


# ─── One document, parsed once ───────────────────────────────────────────────
#
# `doc_parse(offset=N)` 是一条响应自己的 next_action 推荐的续读动作，而每一份续页
# 都会把**整篇文档重新解析一遍**：实测 3.9 MB 的一篇 markdown，翻 8 页要读并解码
# 8 次字节（每次 ~0.2s，其中 charset.charset_is_in_doubt 一项就占 0.196s）。PDF 更
# 贵 —— 一份带 OCR 的扫描件每一页都要把整本再提一遍。
#
# 记账的是"这份字节 + 这组参数"，不是文件名：键里有 mtime_ns 与 size，所以文件被改
# 就无法命中旧条目。剩下的那一种碰撞（一次编辑恰好既不改变字节数也不改变 mtime）不
# 是缓存策略能治的，写在这里而不是假装处理过。
#
# 只记成功的那一次（error 为空且有正文）：失败要么便宜要么要求调用方换参数，把它钉
# 在记忆里只是让同一个报错多活一会儿。密码不进键 —— 那是文档串，进键就等于在进程
# 里多留一份明文；进键的是它的摘要。
#
# 记的是**内存里**的整篇文本，随进程走；不落 cache.db（那份文档可能刚被 password= 解
# 开）。预算按字符记，超了就从头退。

_PARSED_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_PARSED_CACHE_LOCK = threading.Lock()
_PARSED_MAX_ENTRIES = 8
# 单进程里同时最多留 40M 字符（ASCII 正文约 40 MB；纯中文按 4 字节算更多，所以这一
# 颗数是在"一次大 PDF 全篇"和"几篇长文"之间取的折中）。
_PARSED_MAX_TOTAL_CHARS = 40_000_000
_parsed_total_chars = 0


def _parse_and_capture_notes(file_path: str, encoding: str, pages: str | None,
                             password: str | None) -> tuple[str, str, dict, list[str]]:
    """解析一次，并把**这一次**说过的附注一起带回来（正文、报错、extras、附注）。

    附注桶是每次调用装的，而 `_note_text` 对没装桶的人整段消失：装桶的是 MCP 那一层
    （`call_tool` 每次都装），没装桶的是直接 import 的人 —— 测试、embedder。放任它记一条
    "没有解释"的正文进缓存，之后每一次命中都跟着少一句话，于是这条缺陷就改成看调用顺序。
    所以解析永远在自己的桶里跑；桶里已有的东西不算进来（只取本次新增的那一段），否则同一发
    里上一个文件说过的话会被记到这一个文件头上。
    """
    from dhole_mcp.context import _ARG_NOTES, _arg_notes_scope
    bucket = _ARG_NOTES.get()
    if bucket is not None:
        before = len(bucket)
        content, error, extras = parse_file_detailed(file_path, encoding, pages, password)
        return content, error, extras, list(bucket[before:])
    with _arg_notes_scope() as own:
        content, error, extras = parse_file_detailed(file_path, encoding, pages, password)
        return content, error, extras, list(own)


def _replay_arg_notes(notes: list[str]) -> None:
    """把记下过的附注重新挂到这一发调用上。

    附注记在 ContextVar 里，而那份桶是**每次调用**的：命中缓存时解析根本没跑，于是
    桶是空的，交回的正文里明明写着 `[merged-cell table: …]`、`metadata.figures` 明明
    带着账，`summary` 却一句都不说。两次调用读起来一模一样，而其中一次什么都没做过 ——
    与 R4 那条"缓存回放冒充新核验"是同一种形状，只是这次丢的是解释不是时间戳。
    """
    from dhole_mcp.context import _ARG_NOTES
    bucket = _ARG_NOTES.get()
    if bucket is None or not notes:
        return
    for note in notes:
        if note and note not in bucket:
            bucket.append(note)


def parse_file_cached(file_path: str, encoding: str = "",
                      pages: str | None = None,
                      password: str | None = None) -> tuple[str, str, dict]:
    """``parse_file_detailed`` 的结果按内容记账复用。返回值形状完全一致。

    Never raises（和它包着的那颗一样）。
    """
    global _parsed_total_chars
    key = _parsed_cache_key(file_path, encoding, pages, password)
    if key is not None:
        with _PARSED_CACHE_LOCK:
            hit = _PARSED_CACHE.get(key)
            if hit is not None:
                _PARSED_CACHE.move_to_end(key)
                _replay_arg_notes(hit[2])
                return hit[0], "", _copy_extras(hit[1])
    content, error, extras, noted = _parse_and_capture_notes(
        file_path, encoding, pages, password)
    if key is not None and not error and content:
        # 交回与记下的必须是**不同**的 dict：解析出来的那一份直接给出去过一次，调用方
        # 改过它的 metadata，之后每一次命中的都是被改过的那份 —— 而命中看起来与首次
        # 完全一样，没有一条字段会说"这是复用"。
        stored = _copy_extras(extras)
        # 这次解析说过的附注与这份正文是同一件事的两半，所以要一起记账、一起回放。
        with _PARSED_CACHE_LOCK:
            _PARSED_CACHE[key] = (content, stored, noted)
            _parsed_total_chars += len(content)
            while _PARSED_CACHE and (_parsed_total_chars > _PARSED_MAX_TOTAL_CHARS
                                     or len(_PARSED_CACHE) > _PARSED_MAX_ENTRIES):
                dropped = _PARSED_CACHE.popitem(last=False)[1]
                _parsed_total_chars -= len(dropped[0])
        return content, error, _copy_extras(stored)
    return content, error, extras


def _parsed_cache_key(file_path: str, encoding: str, pages: str | None,
                      password: str | None) -> tuple | None:
    """(哪份字节, 怎么解) —— stat 读不到就不记，让调用方走一次真解析。"""
    try:
        absolute = os.path.abspath(os.path.expanduser(file_path))
        st = os.stat(absolute)
    except (OSError, ValueError):
        return None
    digest = hashlib.blake2b((password or "").encode("utf-8", "surrogatepass"),
                             digest_size=16).digest()
    return (absolute, st.st_mtime_ns, st.st_size, (encoding or "").lower(),
            pages or "", digest)


def _copy_extras(extras: dict) -> dict:
    """交回一份新的 extras：调用方会把 metadata 复制走、把 toc 塞进 ResponseModel，
    但共享同一个 dict 意味着下一次命中读到的可能是别人改过的那一份。"""
    out = dict(extras or {})
    if isinstance(out.get("metadata"), dict):
        out["metadata"] = dict(out["metadata"])
    if isinstance(out.get("table_of_contents"), list):
        out["table_of_contents"] = list(out["table_of_contents"])
    return out


def _parse_html(file_path: str, encoding: str = "") -> tuple[str, dict]:
    """Parse HTML file using trafilatura + markdownify (existing chain).

    Returns (content, extras); see _decode_body for the encoding fields.
    """
    with open(file_path, "rb") as f:
        body = f.read()
    # The document's own <meta charset> is a hint; an explicit caller encoding
    # outranks it. decode_file_bytes falls back to detection if either fails.
    html, extras = _decode_body(body, encoding or _html_declared_charset(body),
                               asked=encoding)
    from dhole_mcp.trafilatura_extractor import extract_content_from_html
    result = extract_content_from_html(html, file_path, "markdown")
    if result:
        return result, extras
    # Fallback: markdownify on the raw HTML
    try:
        from markdownify import markdownify
        return markdownify(html, heading_style="ATX").strip(), extras
    except Exception:
        return html[:50000], extras  # last resort: raw HTML truncated


def _parse_structured_text(file_path: str, ext: str,
                           encoding: str = "") -> tuple[str, str, dict]:
    """Read a .json/.yaml/.yml file, and say so when it is not what it claims.

    There is no conversion to do — the text is already readable — so the only
    thing worth adding over a file read is the check. "this .json is not valid
    JSON, here is the parser's complaint" answers the question the caller had
    when they handed a config file to a document tool; returning the bytes with a
    shrug would answer nothing.
    """
    text, extras = _read_text_file(file_path, encoding)
    if ext == ".json":
        import json as _json

        try:
            _json.loads(text)
        except ValueError as e:
            return "", f"{ext} file is not valid JSON: {str(e)[:180]}", extras
        return text, "", extras

    try:
        import yaml
    except ImportError:
        # PyYAML is not a declared dependency of this package. Without it the
        # file is still returned (reading it is the caller's goal) — only the
        # validity check is skipped, so the absence costs a check, not a file.
        return text, "", extras
    try:
        list(yaml.safe_load_all(text))  # a generator: errors surface on iteration
    except Exception as e:
        return "", f"{ext} file is not valid YAML: {str(e)[:180]}", extras
    return text, "", extras


_A_T_RE = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.DOTALL)
#: A LAYOUT field — slide number, date, footer placeholder. It carries a real
#: `<a:t>` (the number itself), so it must be removed before text is collected.
_A_FLD_RE = re.compile(r"<a:fld\b[^>]*>.*?</a:fld>|<a:fld\b[^>]*/>", re.DOTALL)
_ENTITIES = {"&quot;": '"', "&apos;": "'"}

#: A slide PART name. The number in it is a filename, not a position.
_PPTX_SLIDE_PART_RE = re.compile(r"ppt/slides/slide\d+\.xml")
#: `<p:sldId id="256" r:id="rId2"/>` — the deck's play order, in document order.
_SLD_ID_RE = re.compile(r"<p:sldId\b[^>]*\br:id=\"([^\"]+)\"")
_REL_TAG_RE = re.compile(r"<Relationship\b[^>]*/?>")
_REL_ID_RE = re.compile(r"\bId=\"([^\"]+)\"")
_REL_TARGET_RE = re.compile(r"\bTarget=\"([^\"]+)\"")


def _pptx_filename_order(names: list[str]) -> list[str]:
    """The number inside each part name — the fallback, not the answer."""
    return sorted(
        names, key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))


def _pptx_declared_order(zf, available: list[str]) -> list[str]:
    """The play order `ppt/presentation.xml` declares, or [] if it cannot be read.

    The number inside `slideN.xml` is a FILENAME, not a position. Dragging a
    slide in PowerPoint rewrites the `p:sldIdLst` list in presentation.xml and
    leaves every filename exactly where it was — so ordering by filename answers
    a different question than "what order does this deck play in", and the wrong
    answer looks completely normal. Measured on a hand-built deck whose id list
    puts slide2 first: the reader emitted slide1 then slide2, with nothing in
    the output a caller could use to notice. A wrong answer that reads as a
    right one is the failure shape this project treats as worse than an error.

    Two parts are needed: the id list, and the rels that turn each `r:id` into a
    part name. Either one missing (third-party exporters do write partial
    packages) returns [] and the caller falls back to filename order rather than
    dropping slides.
    """
    try:
        pres = zf.read("ppt/presentation.xml").decode("utf-8", "replace")
        rels = zf.read("ppt/_rels/presentation.xml.rels").decode("utf-8", "replace")
    except KeyError:
        return []

    targets: dict[str, str] = {}
    for tag in _REL_TAG_RE.findall(rels):
        rid = _REL_ID_RE.search(tag)
        target = _REL_TARGET_RE.search(tag)
        if rid and target:
            targets[rid.group(1)] = target.group(1)

    order: list[str] = []
    for rid in _SLD_ID_RE.findall(pres):
        target = targets.get(rid, "")
        if not target:
            continue
        name = (target.lstrip("/") if target.startswith("/")
                else posixpath.normpath(posixpath.join("ppt", target)))
        if name in available and name not in order:
            order.append(name)
    return order


def _pptx_paragraphs(xml: str) -> list[str]:
    """Text runs grouped per paragraph (`</a:p>`), entities decoded.

    `<a:fld>` placeholders are dropped first. A slide-number or date field is
    LAYOUT, not something the author wrote, and it wraps a real `<a:t>` — so a
    deck with page numbers used to grow a stray number on every slide: measured,
    a body of "第三季度营收 / 机密" came back as "第三季度营收 / 3 / 机密". The
    same filter is what makes the speaker-notes path usable, since every notes
    part carries the slide-number field.
    """
    from xml.sax.saxutils import unescape

    out: list[str] = []
    for chunk in _A_FLD_RE.sub("", xml).split("</a:p>"):
        runs = _A_T_RE.findall(chunk)
        if runs:
            text = unescape("".join(runs), _ENTITIES).strip()
            if text:
                out.append(text)
    return out


def _pptx_notes_part(zf, slide_part: str) -> str:
    """The notes part this slide points at, or "" when it has none.

    Notes live in their own parts (`ppt/notesSlides/notesSlideN.xml`) and the
    link is per-slide, through that slide's OWN rels. The pairing is therefore
    only recoverable one slide at a time — which is why notes are emitted under
    their slide instead of collected at the end: a note moved to the end of the
    output loses the one thing that makes it useful, namely which slide it
    belongs to.
    """
    base, _, leaf = slide_part.rpartition("/")
    try:
        rels = zf.read(f"{base}/_rels/{leaf}.rels").decode("utf-8", "replace")
    except KeyError:
        return ""
    for tag in _REL_TAG_RE.findall(rels):
        if "notesSlide" not in tag:
            continue
        target = _REL_TARGET_RE.search(tag)
        if target:
            return posixpath.normpath(posixpath.join(base, target.group(1)))
    return ""


def _parse_pptx(file_path: str) -> str:
    """.pptx → 每页的文字，靠它本来就是的东西：一个装着 OOXML 的 zip。

    python-pptx 会是第二个依赖，只为读回文本工具本来就能读的那一层（`<a:t>` 里的
    run）。页序取 `ppt/presentation.xml` 的 `p:sldIdLst` —— 拖过页序的稿子里，文件名
    里的数字与播放顺序是两回事，按文件名排会给一个看着完全正常的错答案；读不到才
    退回文件名数字。**讲者备注读进来**（`ppt/notesSlides/`，按每页自己的 rels 挂回
    它那一页）：幻灯片正文常常只有标题级短语，讲稿、数据来源与要点住在备注里，而
    「总结这份 PPT」正是这颗工具最常见的用法。备注标成 `> Notes:` 引用块 —— 不标就
    和正文糊在一起，读的人会把讲者备注当幻灯片内容引用。没有备注的页不写占位符
    （同 `.docx` 那条规矩：只在文件真有的时候才说）。版式与 SmartArt 不进来 ——
    这一点写在输出的第一行里，而不是一句 `Parse error`：一份少了版式的稿件看起来
    和一份完整的稿件长得一样，读的人有权知道自己拿到的是哪种。
    """
    import zipfile

    with zipfile.ZipFile(file_path) as zf:
        all_parts = set(zf.namelist())
        names = [n for n in all_parts if _PPTX_SLIDE_PART_RE.fullmatch(n)]
        if not names:
            return "(no slides found in this .pptx)"
        declared = _pptx_declared_order(zf, names)
        ordered_by_list = bool(declared)
        if declared:
            # A partial id list must not cost a slide: whatever it named comes
            # first, in its order; anything it did not name follows by filename.
            named = len(declared)
            names = declared + _pptx_filename_order(
                [n for n in names if n not in declared])
        else:
            named = 0
            names = _pptx_filename_order(names)
        # 小节标题说的是**这是第几页**，不是包里那个文件叫什么（评审 R02-025）：
        # `## slide2.xml` 是给人粘进讲义的正文里住着一个给工具看的名字，而且那个数字
        # 连位置都不是（播放顺序住在 presentation.xml 的清单里）。标题既然声称位置，
        # 这个位置是从哪儿来的就得写在第一行上，而不是让读者猜这一发有没有走退路。
        order_note = ""
        if not ordered_by_list:
            order_note = (" - order taken from the file names, this package has no "
                          "readable slide list")
        elif named < len(names):
            order_note = (f" - the deck's own list named {named} of {len(names)}, "
                          "the rest ordered by file name")
        lines = [f"[{len(names)} slide(s); text only - layout and diagram text "
                 f"are not included{order_note}]"]
        for i, n in enumerate(names, 1):
            xml = zf.read(n).decode("utf-8", "replace")
            lines.append(f"\n## Slide {i} of {len(names)}")
            body = _pptx_paragraphs(xml)
            lines.extend(body or ["(no text on this slide)"])
            notes_part = _pptx_notes_part(zf, n)
            if notes_part and notes_part in all_parts:
                notes = _pptx_paragraphs(
                    zf.read(notes_part).decode("utf-8", "replace"))
                if notes:
                    lines.append("> Notes: " + notes[0])
                    lines.extend("> " + para for para in notes[1:])
    return "\n".join(lines)


_ODT_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _parse_odt(file_path: str) -> str:
    """.odt → 段落与标题，用 stdlib 解 content.xml。

    ODF 也是一个 zip，正文在 content.xml 的 `text:p` / `text:h` 里；为此引 odfpy
    不值。标题的层级取 `text:outline-level`，表格单元里的段落按段落读出（它们本来就是
    段落）。样式、图片与批注不进来，同样写在第一行里。
    """
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(file_path) as zf:
        raw = zf.read("content.xml") if "content.xml" in zf.namelist() else b""
    if not raw:
        return "(no content.xml in this .odt - is it an OpenDocument text file?)"

    root = ET.fromstring(raw)
    lines: list[str] = []
    for el in root.iter():
        name = el.tag.rsplit("}", 1)[-1]
        if name not in ("p", "h"):
            continue
        text = "".join(el.itertext()).strip()
        if not text:
            continue
        if name == "h":
            lvl = el.get(f"{{{_ODT_TEXT_NS}}}outline-level") or "1"
            try:
                depth = max(1, min(int(lvl) + 1, 6))
            except ValueError:
                depth = 2
            lines.append("#" * depth + " " + text)
        else:
            lines.append(text)
    if not lines:
        return "(the document has no text blocks)"
    return "\n".join(["[OpenDocument text; styles, images and comments are not "
                      f"included - {len(lines)} text block(s) read]"] + lines)


# ─── .docx ────────────────────────────────────────────────────────────────────

#: A header/footer PART. Matched on the part name, not on the bytes: styles.xml
#: and numbering.xml also contain <w:t> and are not something the caller lost.
_HF_PART_RE = re.compile(r"/word/(?:header|footer)\d+\.xml$")


def _docx_blocks(doc):
    """``(kind, block)`` for every body child, in DOCUMENT order.

    ``doc.paragraphs`` and then ``doc.tables`` loses the order BETWEEN the two: a
    table sitting mid-document was printed after the closing paragraph, so prose
    that said "see the table below" pointed past the end of the file. Measured on
    heading / paragraph / table / paragraph, which came back heading / paragraph
    / paragraph / table. Word stores both kinds as ``w:body`` children, so this
    walks them as such. ``kind`` is ``"p"`` or ``"tbl"``.
    """
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "p", Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield "tbl", Table(child, doc)


def _count_notes(blob: bytes, tag: str) -> int:
    """Real notes, not Word's two separator stubs (ids -1 and 0)."""
    ids = re.findall(rb"<w:" + tag.encode() + rb'\b[^>]*w:id="(-?\d+)"', blob)
    return sum(1 for i in ids if int(i) >= 1)


def _docx_dropped_parts(doc) -> list[str]:
    """Rich content this reader does NOT carry, that the file actually holds.

    A .docx can carry headers, footers, footnotes, endnotes, comments and images;
    this parser reads body paragraphs and tables. Saying so is the module's own
    rule for .pptx/.odt ("what a text reader cannot carry is named in the first
    line"), and .docx was the one format that broke it - measured: a footer
    reading "机密 · 页脚里的重要声明" vanished with no note at all, so a document
    missing its footer looked exactly like a complete one.

    Only what is PRESENT is named, so a plain document gets no noise.
    """
    counts = {"header/footer": 0, "footnote": 0, "endnote": 0, "comment": 0,
              "image": 0}
    for part in doc.part.package.parts:
        name = str(part.partname)
        try:
            blob = part.blob
        except Exception:  # a part that will not load is not a claim about content
            continue
        if _HF_PART_RE.search(name):
            # Most documents carry an empty header/footer part; only one with
            # real text is something the caller lost.
            if b"<w:t" in blob:
                counts["header/footer"] += 1
        elif name.endswith("/footnotes.xml"):
            counts["footnote"] = _count_notes(blob, "footnote")
        elif name.endswith("/endnotes.xml"):
            counts["endnote"] = _count_notes(blob, "endnote")
        elif name.endswith("/comments.xml"):
            counts["comment"] = len(re.findall(rb"<w:comment\b", blob))
        elif "/media/" in name:
            counts["image"] += 1
    return [f"{n} {label}(s)" for label, n in counts.items() if n]


_MD_HEADING_RUNGS = 6
_DOCX_DECLARED_BODY = -1


def _docx_outline_value(el) -> int | None:
    """The level a ``w:outlineLvl`` element claims ("" not at all)."""
    from docx.oxml.ns import qn

    if el is None:
        return None
    try:
        val = int(el.get(qn("w:val")))
    except (TypeError, ValueError):
        return None
    return val if 0 <= val < 9 else None


def _docx_declared_levels(doc) -> dict[str, int | None]:
    """``styleId -> the outline level this file declares``, following ``w:basedOn``.

    ``w:outlineLvl`` is OOXML's own answer to "which rung is this line" - the one
    Word's navigation pane reads. It survives what the display name does not: a
    Chinese Word writes `标题 1` in ``w:name`` while the level stays declared, a
    custom style built on Heading 2 inherits its level through ``w:basedOn``, and
    some exporters declare levels 5-9 that a four-name list can never reach.
    """
    from docx.oxml.ns import qn

    own: dict[str, int | None] = {}
    parent: dict[str, str | None] = {}
    for st in doc.styles.element.findall(qn("w:style")):
        if st.get(qn("w:type")) != "paragraph":
            continue
        sid = st.get(qn("w:styleId"))
        if sid is None:
            continue
        ppr = st.find(qn("w:pPr"))
        own[sid] = _docx_outline_value(
            ppr.find(qn("w:outlineLvl")) if ppr is not None else None)
        based = st.find(qn("w:basedOn"))
        parent[sid] = based.get(qn("w:val")) if based is not None else None

    resolved: dict[str, int | None] = {}
    for sid in own:
        level, cur, seen = None, sid, set()
        while cur in own and cur not in seen:
            seen.add(cur)
            if own[cur] is not None:
                level = own[cur]
                break
            cur = parent[cur]
        resolved[sid] = level
    return resolved


def _docx_paragraph_level(paragraph, declared: dict[str, int | None]) -> int | None:
    """The level of this paragraph, from the file - or ``None`` if it declares none.

    A level written on the paragraph itself outranks the style's, and
    ``w:val="9"`` on the paragraph is Word's own spelling of "this line is body
    text": that comes back as ``_DOCX_DECLARED_BODY`` so the caller does not
    re-heading a line the file just disclaimed.
    """
    from docx.oxml.ns import qn

    ppr = paragraph._p.find(qn("w:pPr"))
    el = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
    if el is not None:
        raw = el.get(qn("w:val"))
        if raw is not None and raw.isdigit() and int(raw) >= 9:
            return _DOCX_DECLARED_BODY
        val = _docx_outline_value(el)
        if val is not None:
            return val
    return declared.get(paragraph.style.style_id)


def _docx_outline_note(titles: int, level_ones: int, clamped: int) -> str:
    """What the outline could NOT be carried as, in one clause each ("" if nothing).

    Both cases are collisions between the file and markdown, not mistakes: a Word
    document title is not an outline entry but lands on the same `#` as a level-1
    heading, and Word declares nine levels while markdown has six rungs. Silent
    would read as "this document has no structure to tell apart".
    """
    notes = []
    if titles and level_ones:
        notes.append(f"its document title rides the same # rung as the file's "
                     f"{level_ones} level-1 heading(s)")
    if clamped:
        notes.append(f"{clamped} deep heading(s) flattened to ######, "
                     "markdown has no rung below that")
    return ("; " + "; ".join(notes)) if notes else ""


def _docx_numbering(doc) -> dict[str, dict[int, str]]:
    """``numId -> the marker each level draws``, read from word/numbering.xml.

    `w:numPr` (on the paragraph or its style) says "this line is a list item";
    only the numbering part says WHICH marker that level draws - `bullet`,
    `decimal`, `lowerRoman`, or OOXML's own `none` ("this level draws nothing").
    A numId the part cannot resolve to keeps an empty level map, which the caller
    reads as "a list whose shape is not readable here" rather than a guess.
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn

    blob = None
    for part in doc.part.package.parts:
        if str(part.partname).endswith("/numbering.xml"):
            blob = part.blob
            break
    if blob is None:
        return {}
    try:
        root = parse_xml(blob)
    except Exception:  # a numbering part that will not parse says nothing about shape
        return {}

    abstracts: dict[str, dict[int, str]] = {}
    for an in root.findall(qn("w:abstractNum")):
        per: dict[int, str] = {}
        for lvl in an.findall(qn("w:lvl")):
            try:
                i = int(lvl.get(qn("w:ilvl")) or 0)
            except (TypeError, ValueError):
                continue
            fmt = lvl.find(qn("w:numFmt"))
            if fmt is not None:
                per[i] = (fmt.get(qn("w:val")) or "").strip().lower()
        abstracts[an.get(qn("w:abstractNumId"))] = per

    out: dict[str, dict[int, str]] = {}
    for num in root.findall(qn("w:num")):
        num_id = num.get(qn("w:numId"))
        ref = num.find(qn("w:abstractNumId"))
        if num_id is None or ref is None:
            continue
        out[num_id] = abstracts.get(ref.get(qn("w:val")), {})
    return out


def _docx_style_numbering(doc) -> dict[str, tuple[str, int]]:
    """``styleId -> (numId, ilvl)``, following ``w:basedOn`` when a style inherits it.

    Word documents put the numbering on the style, not on the paragraph, so
    "which list does this line belong to" has to be looked up the same way.
    """
    from docx.oxml.ns import qn

    own: dict[str, tuple[str, int] | None] = {}
    parent: dict[str, str | None] = {}
    for st in doc.styles.element.findall(qn("w:style")):
        if st.get(qn("w:type")) != "paragraph":
            continue
        sid = st.get(qn("w:styleId"))
        if sid is None:
            continue
        ppr = st.find(qn("w:pPr"))
        numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
        num_id = ilvl = None
        if numpr is not None:
            nid = numpr.find(qn("w:numId"))
            lvl = numpr.find(qn("w:ilvl"))
            num_id = nid.get(qn("w:val")) if nid is not None else None
            ilvl = lvl.get(qn("w:val")) if lvl is not None else None
        own[sid] = (num_id, int(ilvl or 0)) if num_id else None
        based = st.find(qn("w:basedOn"))
        parent[sid] = based.get(qn("w:val")) if based is not None else None

    resolved: dict[str, tuple[str, int]] = {}
    for sid, decl in own.items():
        cur, seen = sid, set()
        while cur in own and cur not in seen:
            seen.add(cur)
            if own[cur] is not None:
                resolved[sid] = own[cur]
                break
            cur = parent[cur]
    return resolved


def _docx_paragraph_list(paragraph, style_lists: dict[str, tuple[str, int]],
                         numbering: dict[str, dict[int, str]]) -> tuple[str, int] | None:
    """``(marker, depth)`` this paragraph's file declares, or ``None`` if it declares none.

    The paragraph's own `w:numPr` outranks its style's; `w:ilvl` is the indent
    depth (OOXML defaults it to 0); `w:numId="0"` is how Word REMOVES a line from a
    list, and `w:numFmt="none"` is how it says "this level draws no marker" - both
    come back as no list. Any other resolved format is an ordered item; a declared
    list whose numbering part cannot be read keeps the bullet, because the file
    said "list item" and only the shape is missing.
    """
    from docx.oxml.ns import qn

    ppr = paragraph._p.find(qn("w:pPr"))
    numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
    if numpr is not None:
        nid = numpr.find(qn("w:numId"))
        lvl = numpr.find(qn("w:ilvl"))
        num_id = nid.get(qn("w:val")) if nid is not None else None
        ilvl = lvl.get(qn("w:val")) if lvl is not None else None
    else:
        decl = style_lists.get(paragraph.style.style_id)
        if decl is None:
            return None
        num_id, ilvl = decl
    if not num_id or num_id == "0":
        return None
    try:
        depth = max(int(ilvl or 0), 0)
    except (TypeError, ValueError):
        depth = 0
    fmt = numbering.get(num_id, {}).get(depth, "")
    if fmt == "none":
        return None
    if fmt in ("", "bullet"):
        return "- ", depth
    return "1. ", depth


def _parse_docx(file_path: str) -> str:
    """Parse .docx to Markdown: body paragraphs and tables, in document order.

    The first line names what a text reader cannot carry, the same contract
    .pptx and .odt follow (see _docx_dropped_parts).
    """
    from docx import Document

    doc = Document(file_path)
    declared = _docx_declared_levels(doc)
    numbering = _docx_numbering(doc)
    style_lists = _docx_style_numbering(doc)
    lines: list[str] = []
    n_paras = 0
    n_tables = 0
    n_titles = 0
    n_level_ones = 0
    n_clamped = 0

    for kind, block in _docx_blocks(doc):
        if kind == "tbl":
            rendered = _table_to_markdown(block)
            if not rendered:
                continue
            lines.append("")
            lines.append(rendered)
            lines.append("")
            n_tables += 1
            continue

        text = block.text.strip()
        if not text:
            lines.append("")
            continue
        n_paras += 1
        style = (block.style.name or "").lower()
        level = _docx_paragraph_level(block, declared)
        if level is not None and level >= 0:
            if level < _MD_HEADING_RUNGS:
                lines.append(f"{'#' * (level + 1)} {text}")
                if level == 0:
                    n_level_ones += 1
            else:
                lines.append(f"{'#' * _MD_HEADING_RUNGS} {text}")
                n_clamped += 1
            continue
        listed = _docx_paragraph_list(block, style_lists, numbering)
        if listed is not None:
            marker, depth = listed
            lines.append(f"{'    ' * depth}{marker}{text}")
            continue
        # Nothing in the file declares this line's shape: the display name is all
        # there is to go on, and it still earns the heading or bullet it used to.
        # Losing a heading or a list item to a shape fix is the worse misread.
        if level == _DOCX_DECLARED_BODY:
            lines.append(text)
        elif "heading 1" in style or style == "title":
            if style == "title":
                n_titles += 1
            else:
                n_level_ones += 1
            lines.append(f"# {text}")
        elif "heading 2" in style:
            lines.append(f"## {text}")
        elif "heading 3" in style:
            lines.append(f"### {text}")
        elif "heading 4" in style:
            lines.append(f"#### {text}")
        elif "list" in style:
            lines.append(f"- {text}")
        else:
            lines.append(text)

    body = "\n".join(lines).strip()
    dropped = _docx_dropped_parts(doc)
    tail = (", dropped: " + ", ".join(dropped) if dropped
            else "; headers/footers, footnotes, comments and images not included")
    head = (f"[Word document; {n_paras} paragraph(s), {n_tables} table(s) read"
            f"{_docx_outline_note(n_titles, n_level_ones, n_clamped)}{tail}]")
    return head + "\n\n" + (body or "(no body text)")


# ─── .xlsx ────────────────────────────────────────────────────────────────────

def _xlsx_uncached_formulas(file_path: str) -> dict[tuple[str, int, int], str]:
    """``(sheet, stream row index, column index) -> formula text``, capped rows.

    A second streaming pass with ``data_only=False``. openpyxl's
    ``data_only=True`` hands back the CACHED value of a formula, and a workbook
    written by a library (openpyxl, pandas, most exporters) has no cache to hand
    back: the cell comes back None and renders as an empty column - measured on a
    sheet whose third column was ``=A2*B2``, which printed ``| 3 | 100 |  |`` and
    reads as "the total is blank" rather than "the total is computed".
    """
    from openpyxl import load_workbook

    out: dict[tuple[str, int, int], str] = {}
    wb = load_workbook(file_path, read_only=True, data_only=False)
    try:
        for sheet_name in wb.sheetnames:
            for i, row in enumerate(wb[sheet_name].iter_rows(values_only=True)):
                if i >= _XLSX_MAX_ROWS:
                    break
                for j, value in enumerate(row):
                    if isinstance(value, str) and value.startswith("="):
                        out[(sheet_name, i, j)] = value
    finally:
        wb.close()
    return out


def _xlsx_grid_note(header_cells: list[str], data_rows: list[list[str]],
                    sheet_rows: list[int]) -> str:
    """The ONE wording for "this grid is the sheet's used range, not one table's columns" ("" if not).

    openpyxl pads every row to the widest row on the sheet, so a stray pair like
    F2/G2 arrives as two extra columns and lands in the FIRST data row — which
    reads as though that record had those two fields (R02-024: a 4-column table
    came back 7 wide, three cells titled nothing). The fix is deliberately NOT
    trimming: measured first, an all-empty column means one of two opposite things
    — the gap in front of an off-table summary (persona), or the ONLY separator
    between two side-by-side tables (A:C and E:F). Deleting it would glue two
    records together, a worse misread than the silence. So no cell moves and none
    is dropped; the receipt just says which columns carry no title and where their
    values actually sit, in sheet row numbers (the same coordinates as F2/G2).
    """
    unheaded = [j for j, h in enumerate(header_cells) if not h.strip()]
    if not unheaded:
        return ""
    try:
        from openpyxl.utils import get_column_letter
    except Exception:  # noqa: BLE001 - 到这一步 openpyxl 必然在；不在就没什么可说的
        return ""
    bits = []
    for j in unheaded:
        hit = [r for r, row in zip(sheet_rows, data_rows) if row[j].strip()]
        letter = get_column_letter(j + 1)
        if not hit:
            bits.append(f"{letter} carries no value in any row")
        elif len(hit) == len(data_rows):
            bits.append(f"{letter} holds data in every row - the title is missing, "
                        "the column is not")
        elif len(hit) == 1:
            bits.append(f"{letter} holds a value only in row {hit[0]}")
        else:
            shown = ", ".join(str(r) for r in hit[:5])
            more = f" (+{len(hit) - 5} more rows)" if len(hit) > 5 else ""
            bits.append(f"{letter} holds values only in rows {shown}{more}")
    width = len(header_cells)
    titled = width - len(unheaded)
    return (f"the grid is this sheet's used range, not one table's columns: "
            f"{width} column(s) wide - {titled} carry a title, "
            f"{len(unheaded)} carry no header cell: " + "; ".join(bits)
            + " - no cell was moved or dropped")


def _parse_xlsx(file_path: str) -> str:
    """Parse .xlsx to Markdown tables using openpyxl.

    Three "looks like success" failures are closed here, each measured on a real
    workbook: a formula whose value was never cached is shown as its formula
    rather than a blank; the row cap reports what it left behind; and that note
    says the rows are NOT shown (it used to say "more rows shown" about exactly
    the rows it was dropping, with a count derived from the cap instead of from
    the sheet).
    """
    from openpyxl import load_workbook

    try:
        formulas = _xlsx_uncached_formulas(file_path)
    except Exception:
        # A formula pass that cannot run must not cost the caller the sheet.
        formulas = {}

    wb = load_workbook(file_path, read_only=True, data_only=True)
    try:
        sections: list[str] = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            # Stream with a cap to prevent OOM on huge sheets. The cap is on rows
            # READ; the note below is what keeps it from reading as "that is all".
            raw: list[tuple[int, tuple]] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= _XLSX_MAX_ROWS:
                    break
                raw.append((i, row))
            # A row counts as present when it has a value OR a formula whose cache
            # is missing - the second case is invisible to a values-only read and
            # used to be dropped as "empty".
            kept = [(i, r) for i, r in raw
                    if any(c is not None for c in r)
                    or any((sheet_name, i, j) in formulas for j in range(len(r)))]
            if not kept:
                continue

            uncached: list[str] = []

            def cell_text(row_idx: int, col_idx: int, value) -> str:
                if value is None:
                    formula = formulas.get((sheet_name, row_idx, col_idx))
                    if formula:
                        uncached.append(formula)
                        return formula
                    return ""
                return str(value)

            header_idx, header = kept[0]
            col_count = len(header)
            header_cells = [cell_text(header_idx, j, header[j] if j < len(header) else None)
                            for j in range(col_count)]
            header_str = "| " + " | ".join(header_cells) + " |"
            sep_str = "| " + " | ".join("---" for _ in range(col_count)) + " |"
            data_rows: list[list[str]] = []
            data_sheet_rows: list[int] = []
            for row_idx, row in kept[1:]:
                cells = list(row) + [None] * (col_count - len(row))  # pad short rows
                data_rows.append([cell_text(row_idx, j, cells[j])
                                  for j in range(col_count)])
                # 纸上的行号（1 基）：`kept` 会跳过空行，所以这一颗必须是原来的 row_idx，
                # 不是这一片的第几行 —— 注记里说的"F2"要和用户在 Excel 里点到的那一格同名。
                data_sheet_rows.append(row_idx + 1)
            data_strs = ["| " + " | ".join(r) + " |" for r in data_rows]

            section = (f"## {sheet_name}\n\n"
                       + "\n".join([header_str, sep_str] + data_strs))

            notes: list[str] = []
            if uncached:
                notes.append(f"{len(uncached)} formula cell(s) had no cached value "
                             "and are shown as their formula")
            grid_note = _xlsx_grid_note(header_cells, data_rows, data_sheet_rows)
            if grid_note:
                notes.append(grid_note)
            rendered_rows = 1 + len(data_strs)
            total = getattr(ws, "max_row", None)
            if isinstance(total, int) and total > rendered_rows:
                notes.append(f"{total - rendered_rows} more row(s) not shown, "
                             f"of {total} in the sheet")
            elif not isinstance(total, int) and len(raw) >= _XLSX_MAX_ROWS:
                notes.append("more rows not shown (the sheet has more than "
                             f"{rendered_rows})")
            if notes:
                section += "\n\n... (" + "; ".join(notes) + ")"
            sections.append(section)
    finally:
        wb.close()

    return "\n\n".join(sections) if sections else "(empty workbook)"


def _sniff_delimiter(text: str) -> str:
    """Guess the field separator, defaulting to ','.

    Excel in a comma-decimal locale writes ';', and a semicolon CSV parsed as
    comma-separated puts every row in column 1 - a table that is silently wrong
    rather than visibly empty. Measured against the stdlib sniffer on 24
    samples (single-column, ragged, CRLF, quoted commas, and text containing
    ; | and tab): it only ever raises on genuinely ambiguous single-column
    input, which renders identically whatever the delimiter, so the ',' default
    is safe. Never raises.
    """
    sample = text[:8192]
    if not sample.strip():
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _parse_csv(file_path: str, encoding: str = "") -> tuple[str, dict]:
    """Parse .csv to a Markdown table using stdlib csv.

    Streams rows with a cap (101) to prevent OOM on huge CSV files.
    Returns (content, extras); see _decode_body for the encoding fields.
    """
    text, extras = _read_text_file(file_path, encoding)

    rows = []
    has_more = False
    reader = csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text))
    for i, row in enumerate(reader):
        if i >= 101:  # header + 100 data rows
            has_more = True
            break
        rows.append(row)

    if not rows:
        return "(empty CSV)", extras

    header = rows[0]
    col_count = len(header)
    header_str = "| " + " | ".join(header) + " |"
    sep_str = "| " + " | ".join("---" for _ in range(col_count)) + " |"
    data_strs = []
    for row in rows[1:]:
        cells = row + [""] * (col_count - len(row))
        data_strs.append("| " + " | ".join(cells[:col_count]) + " |")

    result = "\n".join([header_str, sep_str] + data_strs)
    if has_more:
        result += "\n\n... (100+ rows, truncated for display)"
    return result, extras


def _parse_pdf(file_path: str, pages: str | None = None,
               password: str | None = None) -> tuple[str, str, dict]:
    """Parse a local .pdf to Markdown, reusing the URL path's extractor.

    Same pipeline web_read uses for PDF URLs - layout-aware markdown,
    per-page CID-garbage OCR fallback, and the same ``pages`` / ``password``
    knobs - so a local file behaves identically to a fetched one, envelope
    included: the extractor's table_of_contents, metadata and quality_score are
    handed back to the caller instead of being dropped here.

    Why not just hand it to web_read as ``file://``: that tool's URL
    validator is an SSRF guard and rejects non-http(s) schemes by design.
    Reading a path the caller supplied is a different trust boundary - the same
    one this module already crosses for .docx/.xlsx/.csv/.html.

    Returns (content, error, extras) rather than just content, because a PDF
    can fail in ways the other formats cannot (encrypted, scanned with no text
    layer) and because it carries an envelope of its own.
    """
    from dhole_mcp.pdf_extractor import extract_pdf, recover_scanned_pdf

    with open(file_path, "rb") as fh:
        body = fh.read()

    result = extract_pdf(body, extraction_type="markdown",
                         pages=pages, password=password)

    # All-scanned document: run the same OCR recovery the URL path runs. The
    # docstring above promises "a local file behaves identically to a fetched
    # one", and for this branch it did not: the same bytes came back OCR'd over
    # HTTP and "install dhole-mcp[all] to auto-OCR" from disk, on a machine
    # where OCR was installed and working.
    #
    # `pages` / `password` close the last two holes in that same promise. Before
    # them an encrypted PDF could not be read at all, and the refusal told the
    # caller to "pass the correct one via the 'password' option" - an option
    # this tool did not have, so following the advice raised TypeError (measured
    # through the server). A 40-page local PDF also came back whole alongside a
    # 40-entry table_of_contents the caller had no way to act on.
    result = recover_scanned_pdf(result, body, pages=pages, password=password)

    if not result.content_ok:
        return "", f"PDF extraction failed: {result.error or 'no extractable content'}", {}

    extras = {
        "table_of_contents": result.table_of_contents or [],
        "metadata": result.metadata or {},
        "pages_total": result.pages_total,
        "pages_returned": len(result.pages_extracted),
        "quality_score": result.quality_score,
        # The extractor's own verdict, not a guess: _agent_hints defers to it
        # once quality_score is set, so a healthy PDF that reported
        # content_ok=False here would be flagged "do not cite".
        "content_ok": bool(result.content_ok),
    }

    # The extractor's content already opens with `# <title>` (and the author in
    # its metadata header) — `_format_metadata` puts them there, and `ocr_pdf`
    # does the same. Prepending them again printed the title TWICE on every
    # titled PDF parsed from disk, while the same bytes over HTTP printed it
    # once: the two paths disagreed on the same document, which is the one thing
    # this function's docstring promises they won't. Title and author are still
    # in `extras["metadata"]` for callers that want them as fields.
    parts: list[str] = ["\n\n".join(result.content)]

    # Only annotate when something is off - a clean PDF gets no extra noise.
    notes: list[str] = []
    if result.ocr_fallback_used:
        notes.append("OCR fallback used (scanned or CID-garbled pages)")
    if result.quality_score and result.quality_score < 0.6:
        notes.append(f"low extraction quality ({result.quality_score:.2f})")
    if notes:
        parts.append("> " + "; ".join(notes))

    return "\n\n".join(p for p in parts if p).strip(), "", extras


def _table_to_markdown(table) -> str:
    """Convert a python-docx Table to a Markdown table string."""
    rows_data: list[list[str]] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        rows_data.append(cells)

    if not rows_data:
        return ""

    col_count = max(len(r) for r in rows_data)
    # Pad rows to same length
    for r in rows_data:
        while len(r) < col_count:
            r.append("")

    header_str = "| " + " | ".join(rows_data[0]) + " |"
    sep_str = "| " + " | ".join("---" for _ in range(col_count)) + " |"
    data_strs = ["| " + " | ".join(r) + " |" for r in rows_data[1:]]

    return "\n".join([header_str, sep_str] + data_strs)
