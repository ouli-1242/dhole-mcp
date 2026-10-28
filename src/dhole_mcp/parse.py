"""Dhole MCP 的本地文件解析。

把本地文件转成 Markdown，让 agent 不必走网络取回也能读文档。属于 [all] 附加依赖
（python-docx、openpyxl）。依赖缺失时优雅降级：交回一句说得清的报错。

支持的格式：
- .html / .htm / .xhtml → trafilatura + markdownify（复用既有抽取链）
- .docx         → python-docx：按**文档顺序**读标题、段落与表格；正文读取器带不走
                  的东西（页眉/页脚、脚注、批注、图片）写在第一行 —— 与 .pptx/.odt
                  已经在守的同一份契约
- .xlsx / .xlsm → openpyxl：工作表 → Markdown 表格（启用宏的 .xlsm 是同一个 OOXML zip）
- .csv / .tsv  → stdlib csv：→ Markdown 表格（分隔符是嗅探出来的）
- .pdf          → pdf_extractor（与 web_read 处理 PDF URL 同一条管线：版面感知的
                  markdown、扫描件 OCR 兜底、质量分，以及同一组 pages= / password= 旋钮）
- .md / .markdown / .txt → 原样返回，走与 .csv 相同的字符集检测
- .json / .yaml / .yml   → 原样返回，但先校验（解析不了的文件是一句报错）
- .pptx / .odt  → stdlib zip + XML，不要 python-pptx / odfpy：每页幻灯片/每个段落的
                  文字，正文读取器带不走的东西写在输出第一行。幻灯片按稿件自己的
                  **播放顺序**（presentation.xml 的 p:sldIdLst）出来，不按文件名 ——
                  slideN.xml 里的数字是文件名，不是位置；讲者备注写在它所属那页下面，
                  标成 `> Notes:`。`<a:fld>` 版式字段（页码、日期）不是正文，丢掉。

2007 之前的 Office 文件（.doc / .xls / .ppt）是 OLE2/CFB 而不是 zip：拒绝它们靠的
是**魔数**而不是扩展名，于是一份改名成 .docx 的 .doc 会被按它真正的样子点名，而不是
掉进 python-docx 的 PackageNotFoundError。拒绝句里教了怎么转换。

文本格式从前用「用你自己的文件工具读」拒掉，那句话预设调用方手里有那样的工具。只会
说 MCP 的客户端要够到本地文档，只有这颗工具这一条路，或者根本够不到 —— 所以那句拒绝
是给我们自己定的规矩，不是给调用方的服务。
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

# 支持的扩展名。`.xlsm`（启用宏的 OOXML）与 `.tsv` 在这里，是因为它们不花任何代价，
# 只是一直没被列进来：前者是 openpyxl 本来就会打开的那个 zip，后者是同一个 csv 读取器，
# 而它的分隔符嗅探候选里本来就有 "\t"。
SUPPORTED_EXTENSIONS = {".html", ".htm", ".xhtml",
                        ".docx", ".xlsx", ".xlsm", ".csv", ".tsv",
                        ".pdf", ".pptx", ".odt",
                        ".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}
# 完全不需要转换的格式。它们从前被「自己读文件」拒掉，而那句话假设调用方**有**文件工具：
# 只会说 MCP 的客户端够到本地文档只有一条路，就是这一颗。在那里拒掉它是给我们自己定的
# 规矩，不是给用户的。
TEXTUAL_EXTENSIONS = {".md", ".markdown", ".txt"}
# 调用方可能想校验、而不只是读的文本。
STRUCTURED_TEXT_EXTENSIONS = {".json", ".yaml", ".yml"}


# 解析的最大文件大小（50 MB），防止大文件把内存打爆
MAX_PARSE_FILE_SIZE = 50 * 1024 * 1024


#: OLE2 / 复合文件二进制签名。Word 97-2003 的 `.doc`、Excel 的 `.xls` 与 PowerPoint 的
#: `.ppt` 都以这八个字节开头，建立在 CFB 之上的其他东西（msi、msg……）也一样。现代的
#: .docx/.xlsx/.pptx 是 ZIP（"PK"），所以只要扩展名说的是真话，这个魔数就永远不会出现 ——
#: 这正是这项检查跑在扩展名决定任何事之前的原因。
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: 这颗读取器**可能**被递到的 CFB 名字 ->（说清文件到底是什么的那句话，该转成的现代
#: 格式）。嗅探成 CFB 的其他东西拿到通用那句，因为 `.msi` 不是 Office 文档，不该被教
#: 「另存为 .docx」。
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
    """文件以 OLE2/CFB 签名开头时返回 True。永不抛异常。"""
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
    """OLE2/CFB 文件的拒绝句，按它真正的样子点名。

    它取代的两种形状都更糟。`.doc` 从前得到的答复是「改名成 .txt」，那不可能奏效 ——
    改名不改字节；而一份改名成 `.docx` 的 CFB 文件（非常常见的一次尝试）会掉进
    python-docx，交回 ``Parse error (.docx): PackageNotFoundError: Package not
    found at '...'`` —— 一句内部异常，既不说真正是什么格式，也不给任何出路。两种都
    实测过。

    返回 ``(error, way_forward)``：判决与那句告诉调用方该做什么的话，于是出路永远不需要
    第二位作者。
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


#: 一张表最多渲染的行数，含表头。读取在这里停下是刻意的（10 万行的工作表会把调用方的
#: 上下文打爆，而不是帮到它）—— 但帽位留下的东西会被**申报**。注记从前写「99 more rows
#: shown」，而那 99 行恰恰是被丢掉的那些，在一张还多 400 行的表上：一次读起来像完整的
#: 截断，是这里能有的最糟结果。
_XLSX_MAX_ROWS = 101


# ─── 字节级解码 ──────────────────────────────────────────────────────────────
#
# 本地文件没有 HTTP 头来声明字符集，而 .csv/.html 从前按 UTF-8 加 errors="replace" 读。
# 经 MCP 服务器实测：一份 90 字节的 GBK CSV —— Excel 的「CSV (逗号分隔)」在中文 Windows
# 上写出来的那种 —— 解析成 "| ���� | ���� |"，却仍然报 content_ok=true、error=""。看起来
# 像成功的乱码是这里能有的最糟结果。
#
# 候选顺序与损伤分住在 dhole_mcp.charset，紧挨着 HTTP 那条路上同一个决定的版本，于是
# 两边不会漂。

_HTML_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""", re.IGNORECASE)


def _html_declared_charset(body: bytes) -> str:
    """文档为自己点名的字符集；一个字集都没点名时返回 ''。

    只扫头部：浏览器要求 <meta charset> 落在前 1024 字节内才算数，而扫整份文件有匹配
    到恰好包含 "charset=" 的正文的风险。一个与字节矛盾的声明无害 —— 声明的名字严格
    解码失败时，charset.decode_file_bytes 会落到检测上。
    """
    match = _HTML_CHARSET_RE.search(body[:2048])
    return match.group(1).decode("ascii", errors="ignore") if match else ""


def _decode_body(body: bytes, declared: str = "", asked: "str | None" = None
                 ) -> tuple[str, dict]:
    """解码文件字节并把信封字段装好。见 charset.decode_file_bytes。

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
    """把本地文件按字节读进来再解码。见 charset.decode_file_bytes。"""
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
    """把本地文件解析成 Markdown。

    返回 (content: str, error: str)。成功时 error 为空。
    失败时 content 为空，error 说明出了什么事。
    永不抛异常。

    ``pages`` / ``password`` 只对 .pdf 起作用；见 parse_file_detailed。
    """
    content, error, _extras = parse_file_detailed(file_path, encoding, pages, password)
    return content, error


def parse_file_detailed(file_path: str, encoding: str = "",
                        pages: str | None = None,
                        password: str | None = None) -> tuple[str, str, dict]:
    """把本地文件解析成 Markdown，外加它能填的信封字段。

    返回 (content, error, extras)。PDF 的 ``extras`` 带着 table_of_contents /
    metadata / quality_score / content_ok —— 本地 PDF 该报出与 URL 取回同一份信封，
    而它从前报 toc=[]、metadata={}，URL 那条路两样都报。.html/.csv 的 extras 带着
    ``encoding``（字节按哪个字符集解码）以及文本读坏时的 ``decode_damage``。其他格式
    为空。永不抛异常。

    ``encoding`` 覆盖自动检测；除非调用方比字节更清楚，否则留空。``pages`` /
    ``password`` 只对 .pdf 有效，语义同 web_read；其他格式会用一行申报说明这一点，
    而不是答得像什么都没问过。
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

    # 展开 ~ 并解析成绝对路径
    file_path = os.path.expanduser(file_path)
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(file_path)

    if not os.path.exists(file_path):
        # 同一句话在这条路上还有第二个作者（server.doc_parse 那句），两处都要遮蔽：
        # 走到这里说明文件在解析那一刻不在了，而 file_path 已经是绝对形式，里面
        # 通常带着本机的根。（2026-10-09 审计 D11；见 paths.mask_machine_paths。）
        from dhole_mcp import paths as _paths
        return "", _paths.mask_machine_paths(f"File not found: {file_path}"), {}

    # 防内存打爆：拒掉大于 50 MB 的文件
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

    # 字节先于扩展名作决定。CFB 文件永远不是现代 Office 文档，所以一项检查同时罩住
    # 普通的 `.doc` 与一份其实是改名 `.doc` 的 `.docx` —— 这两种形状从前拿到的都是
    # 没用的答复（一句不可能奏效的改名建议，以及 python-docx 的 PackageNotFoundError）。
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


# ─── 一份文档，只解析一次 ────────────────────────────────────────────────────
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

    永不抛异常（和它包着的那颗一样）。
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
    """用 trafilatura + markdownify（既有链条）解析 HTML 文件。

    返回 (content, extras)；编码字段见 _decode_body。
    """
    with open(file_path, "rb") as f:
        body = f.read()
    # 文档自己的 <meta charset> 是提示；调用方显式点名的编码压过它。两者都失败时
    # decode_file_bytes 落到检测上。
    html, extras = _decode_body(body, encoding or _html_declared_charset(body),
                               asked=encoding)
    from dhole_mcp.trafilatura_extractor import extract_content_from_html
    result = extract_content_from_html(html, file_path, "markdown")
    if result:
        return result, extras
    # 兜底：对原始 HTML 跑 markdownify
    try:
        from markdownify import markdownify
        return markdownify(html, heading_style="ATX").strip(), extras
    except Exception:
        return html[:50000], extras  # 最后手段：原始 HTML 截断


def _parse_structured_text(file_path: str, ext: str,
                           encoding: str = "") -> tuple[str, str, dict]:
    """读 .json/.yaml/.yml 文件，并在它不是它声称的东西时说出来。

    没有转换可做 —— 文本本来就能读 —— 所以比一次普通文件读多出来的、唯一值得做的事
    就是校验。「这份 .json 不是合法 JSON，解析器的原话在这」回答的正是调用方把一份
    配置文件递给文档工具时的问题；耸耸肩把字节还回去，什么也没回答。
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
        # PyYAML 不是本包声明的依赖。没有它文件照样返回（读它才是调用方的目标）——
        # 跳过的只是校验，所以这份缺失的代价是一次检查，不是一个文件。
        return text, "", extras
    try:
        list(yaml.safe_load_all(text))  # 生成器：错误在迭代时才浮出来
    except Exception as e:
        return "", f"{ext} file is not valid YAML: {str(e)[:180]}", extras
    return text, "", extras


_A_T_RE = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.DOTALL)
#: 一个版式字段 —— 页码、日期、页脚占位。它带着真正的 `<a:t>`（数字本身），所以必须在
#: 收集正文之前删掉。
_A_FLD_RE = re.compile(r"<a:fld\b[^>]*>.*?</a:fld>|<a:fld\b[^>]*/>", re.DOTALL)
_ENTITIES = {"&quot;": '"', "&apos;": "'"}

#: 幻灯片 PART 名。里面的数字是文件名，不是位置。
_PPTX_SLIDE_PART_RE = re.compile(r"ppt/slides/slide\d+\.xml")
#: `<p:sldId id="256" r:id="rId2"/>` —— 稿件的播放顺序，按文档顺序。
_SLD_ID_RE = re.compile(r"<p:sldId\b[^>]*\br:id=\"([^\"]+)\"")
_REL_TAG_RE = re.compile(r"<Relationship\b[^>]*/?>")
_REL_ID_RE = re.compile(r"\bId=\"([^\"]+)\"")
_REL_TARGET_RE = re.compile(r"\bTarget=\"([^\"]+)\"")


def _pptx_filename_order(names: list[str]) -> list[str]:
    """每个 part 名里的数字 —— 这是退路，不是答案。"""
    return sorted(
        names, key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))


def _pptx_declared_order(zf, available: list[str]) -> list[str]:
    """`ppt/presentation.xml` 声明的播放顺序；读不到时返回 []。

    `slideN.xml` 里的数字是**文件名**，不是位置。在 PowerPoint 里拖动一页会重写
    presentation.xml 的 `p:sldIdLst` 清单，而每个文件名原地不动 —— 于是按文件名排序
    回答的是另一个问题，不是「这份稿子按什么顺序播放」，而那个错答案看起来完全正常。
    在一份手工构造、id 清单把 slide2 排在最前的稿子上实测：读取器先吐 slide1 再吐
    slide2，输出里没有任何东西能让调用方察觉。一个读起来像对的错答案，正是本项目视为
    比报错更糟的失败形状。

    需要两个 part：id 清单，以及把每个 `r:id` 翻成 part 名的 rels。缺任何一个
    （第三方导出器确实会写出残缺包）都返回 []，调用方退回文件名顺序，而不是丢掉幻灯片。
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
    """按段落（`</a:p>`）分组的文字 run，实体已解码。

    `<a:fld>` 占位先删掉。页码或日期字段是**版式**，不是作者写的东西，而它包着真正的
    `<a:t>` —— 于是一份带页码的稿子从前每页都长出一个多余的数字：实测正文
    「第三季度营收 / 机密」交回来是「第三季度营收 / 3 / 机密」。同一个过滤器也是讲者
    备注那条路能用的原因，因为每个备注 part 都带着页码字段。
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
    """这一页指向的备注 part；没有时返回 ""。

    备注住在自己的 part 里（`ppt/notesSlides/notesSlideN.xml`），链接按页走、经过那一页
    **自己的** rels。所以配对只能一页一页地还原 —— 这就是备注写在所属幻灯片下面、而不是
    收集到末尾的原因：一条被挪到输出末尾的备注，丢掉的正是让它有用的那件事，即它属于
    哪一页。
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
            # 残缺的 id 清单不许让一页消失：它点到的先按它的顺序排，没点到的随后按文件名排。
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

#: 页眉/页脚 PART。按 part 名匹配，不按字节：styles.xml 与 numbering.xml 里也有
#: <w:t>，但它们不是调用方丢掉的东西。
_HF_PART_RE = re.compile(r"/word/(?:header|footer)\d+\.xml$")


def _docx_blocks(doc):
    """按**文档顺序**给出每个正文子项的 ``(kind, block)``。

    先 ``doc.paragraphs`` 再 ``doc.tables`` 会丢掉两类之间的顺序：夹在文档中间的表格
    被印在收尾段落之后，于是写着「见下表」的正文指到了文件末尾之外。实测
    标题/段落/表格/段落 交回来是 标题/段落/段落/表格。Word 把两类都存成 ``w:body``
    的子项，所以这里就按它们本来的样子遍历。``kind`` 是 ``"p"`` 或 ``"tbl"``。
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
    """真正的批注数，不算 Word 那两颗分隔桩（id 为 -1 与 0）。"""
    ids = re.findall(rb"<w:" + tag.encode() + rb'\b[^>]*w:id="(-?\d+)"', blob)
    return sum(1 for i in ids if int(i) >= 1)


def _docx_dropped_parts(doc) -> list[str]:
    """这颗读取器**带不走**、而文件确实装着的富内容。

    一份 .docx 可以带页眉、页脚、脚注、尾注、批注与图片；这个解析器只读正文段落与表格。
    把这件事说出来，是本模块给 .pptx/.odt 定的规矩（「正文读取器带不走的东西写在第一
    行」），而 .docx 是唯一破坏它的格式 —— 实测：一条写着「机密 · 页脚里的重要声明」的
    页脚一声不响地消失了，于是一份缺了页脚的文档看起来与完整的一模一样。

    只有**确实存在**的东西才点名，所以一份普通文档不会多出噪声。
    """
    counts = {"header/footer": 0, "footnote": 0, "endnote": 0, "comment": 0,
              "image": 0}
    for part in doc.part.package.parts:
        name = str(part.partname)
        try:
            blob = part.blob
        except Exception:  # 装不进来的 part 不是关于正文的一项声明
            continue
        if _HF_PART_RE.search(name):
            # 大多数文档都带一个空的页眉/页脚 part；只有真装着正文的那个才是调用方丢掉的东西。
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
    """``w:outlineLvl`` 元素声称的层级；没有该元素时返回 None。"""
    from docx.oxml.ns import qn

    if el is None:
        return None
    try:
        val = int(el.get(qn("w:val")))
    except (TypeError, ValueError):
        return None
    return val if 0 <= val < 9 else None


def _docx_declared_levels(doc) -> dict[str, int | None]:
    """``styleId -> 这份文件声明的 outline 层级``，顺着 ``w:basedOn`` 追。

    ``w:outlineLvl`` 是 OOXML 自己对「这一行是哪一级」的回答 —— Word 的导航窗格读的
    就是它。它能活过显示名活不过的事：中文 Word 在 ``w:name`` 里写 `标题 1`，而层级
    照样声明着；建在 Heading 2 上的自定义样式通过 ``w:basedOn`` 继承层级；一些导出器
    还会声明四名单永远够不到的 5-9 级。
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
    """这一段的层级，由文件给出 —— 文件没声明时返回 ``None``。

    写在段落自己身上的层级压过样式的；而段落上的 ``w:val="9"`` 是 Word 自己拼写
    「这一行是正文」的方式：它回 ``_DOCX_DECLARED_BODY``，于是调用方不会把文件刚否认
    过的一行重新变成标题。
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
    """这份大纲**没法**按什么带出来，每条一句（没有则返回 ""）。

    两种情况都是文件与 markdown 之间的碰撞，不是错误：Word 的文档标题不是大纲条目，
    却与一级标题落在同一个 `#` 上；Word 声明九级，而 markdown 只有六级台阶。沉默会被
    读成「这份文档没有可分辨的结构」。
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
    """``numId -> 每一级画出的标记``，从 word/numbering.xml 读。

    `w:numPr`（在段落或它的样式上）说「这一行是列表项」；只有编号 part 说那一级**画**
    哪种标记 —— `bullet`、`decimal`、`lowerRoman`，或 OOXML 自己的 `none`（「这一级
    什么都不画」）。part 解析不了的 numId 保留一张空层级表，调用方读到的是「一份形状
    在这里读不出来的列表」，而不是一个猜测。
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
    except Exception:  # 解不开的编号 part 什么也没说，与形状无关
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
    """``styleId -> (numId, ilvl)``，样式继承时顺着 ``w:basedOn`` 追。

    Word 文档把编号挂在样式上，不是段落上，所以「这一行属于哪个列表」也得按同一条路查。
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
    """这一段所属文件声明的 ``(标记, 深度)``；文件没声明时返回 ``None``。

    段落自己的 `w:numPr` 压过样式的；`w:ilvl` 是缩进深度（OOXML 缺省为 0）；
    `w:numId="0"` 是 Word **移除**一行列表身份的方式，`w:numFmt="none"` 是它说
    「这一级不画标记」的方式 —— 两者都回成没有列表。其他任何解析出来的格式都是有序列
    表项；一份声明了列表、但编号 part 读不出来的，保留圆点，因为文件说了「列表项」，
    缺的只是形状。
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
    """把 .docx 解析成 Markdown：正文段落与表格，按文档顺序。

    第一行点名正文读取器带不走的东西，与 .pptx、.odt 守同一份契约（见 _docx_dropped_parts）。
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
        # 文件里没有任何东西声明这一行的形状：显示名是仅有的线索，它仍然挣得从前那顶
        # 标题或那个圆点。为一次形状修复丢掉一个标题或一条列表项，是更糟的误读。
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
    """``(工作表, 流式行号, 列号) -> 公式文本``，行数受帽。

    用 ``data_only=False`` 再流式扫一遍。openpyxl 的 ``data_only=True`` 交回公式的
    **缓存**值，而由库（openpyxl、pandas、大多数导出器）写出的工作簿没有缓存可交：
    单元格回 None，渲染成空列 —— 在一张第三列是 ``=A2*B2`` 的表上实测，它印出
    ``| 3 | 100 |  |``，读起来是「合计是空的」，而不是「合计是算出来的」。
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
    """「这片网格是工作表的已用区域，不是某一张表的列」的唯一措辞（不适用时返回 ""）。

    openpyxl 把每一行补齐到表上最宽的那一行，于是一对落在 F2/G2 的零散值会变成两列
    多出来的列，并落进**第一行**数据 —— 读起来像那条记录真有这两个字段（R02-024：
    一张 4 列的表交回来 7 列宽，三个格子的标题是空的）。修法刻意**不**做裁剪：先量过，
    一整列全空意味着两种相反的事之一 —— 表外摘要前的空档（persona），或两张并排表
    （A:C 与 E:F）之间**唯一**的分隔。删掉它会把两条记录粘在一起，比沉默更糟的误读。
    所以没有格子被移动或丢弃；回执只说哪些列没有标题、它们的值实际坐在哪，用工作表
    行号（与 F2/G2 同一套坐标）。
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
    """用 openpyxl 把 .xlsx 解析成 Markdown 表格。

    三种「看起来像成功」的失败在这里收口，每一种都在真实工作簿上量过：值从未缓存的
    公式显示成公式而不是空白；行帽申报它留下了什么；那条注记说这些行**没有**显示
    （它从前对它正在丢的那些行写「more rows shown」，而数字是从帽位推出来的，不是从
    工作表）。
    """
    from openpyxl import load_workbook

    try:
        formulas = _xlsx_uncached_formulas(file_path)
    except Exception:
        # 一趟跑不起来的公式扫描不许让调用方失去整张表。
        formulas = {}

    wb = load_workbook(file_path, read_only=True, data_only=True)
    try:
        sections: list[str] = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            # 流式读并设帽，防止大表把内存打爆。帽在**读取**的行数上；下面那条注记才是
            # 让它不会被读成「就这些了」的东西。
            raw: list[tuple[int, tuple]] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= _XLSX_MAX_ROWS:
                    break
                raw.append((i, row))
            # 一行有值、或有公式而缓存缺失，就算存在 —— 第二种对只读值的扫描不可见，
            # 从前被当成“空行”丢掉。
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
                cells = list(row) + [None] * (col_count - len(row))  # 补齐短行
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
    """猜字段分隔符，默认 ','。

    逗号小数地区的 Excel 写 ';'，而一份按逗号解析的分号 CSV 会把每一行都塞进第 1 列 ——
    一张悄悄错的表，而不是一张看得出空的表。在 24 份样本（单列、参差、CRLF、带引号
    逗号、含 ; | 与制表符的文本）上与 stdlib 嗅探器对量：它只在真正含糊的单列输入上
    抛异常，而那种输入无论分隔符是什么都渲染成同一个样子，所以 ',' 这个默认是安全的。
    永不抛异常。
    """
    sample = text[:8192]
    if not sample.strip():
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _parse_csv(file_path: str, encoding: str = "") -> tuple[str, dict]:
    """用 stdlib csv 把 .csv 解析成 Markdown 表格。

    流式读行并设帽（101），防止大 CSV 把内存打爆。
    返回 (content, extras)；编码字段见 _decode_body。
    """
    text, extras = _read_text_file(file_path, encoding)

    rows = []
    has_more = False
    reader = csv.reader(io.StringIO(text), delimiter=_sniff_delimiter(text))
    for i, row in enumerate(reader):
        if i >= 101:  # 表头 + 100 行数据
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
    """把本地 .pdf 解析成 Markdown，复用 URL 那条路的抽取器。

    与 web_read 处理 PDF URL 同一条管线 —— 版面感知的 markdown、逐页 CID 乱码的 OCR
    兜底，以及同一组 ``pages`` / ``password`` 旋钮 —— 所以本地文件与取回的一份行为一致，
    信封也一样：抽取器的 table_of_contents、metadata 与 quality_score 交回给调用方，
    而不是在这里丢掉。

    为什么不直接把它当 ``file://`` 交给 web_read：那颗工具的 URL 校验器是 SSRF 守卫，
    按设计拒绝非 http(s) 协议。读调用方给的路径是另一条信任边界 —— 本模块为
    .docx/.xlsx/.csv/.html 早就跨过的那一条。

    返回 (content, error, extras) 而不只是 content，因为 PDF 会以其他格式不会的方式
    失败（加密、扫描件没有文字层），也因为它带着自己的信封。
    """
    from dhole_mcp.pdf_extractor import extract_pdf, recover_scanned_pdf

    with open(file_path, "rb") as fh:
        body = fh.read()

    result = extract_pdf(body, extraction_type="markdown",
                         pages=pages, password=password)

    # 整份都是扫描件：跑 URL 那条路跑的同一个 OCR 补救。上面的 docstring 承诺「本地文件
    # 与取回的一份行为一致」，而这一支从前没有做到：同一批字节走 HTTP 回来是 OCR 过的
    # 正文，从磁盘读却回来一句「install dhole-mcp[all] to auto-OCR」，而机器上 OCR 装了、
    # 也在工作。
    #
    # `pages` / `password` 补上同一个承诺的最后两个洞。在它们之前，一份加密 PDF 根本
    # 读不了，而拒绝句教调用方「pass the correct one via the 'password' option」——
    # 一颗这颗工具根本没有的参数，照着做当场 TypeError（经服务器实测）。一份 40 页的
    # 本地 PDF 也会整份交回来，连同一条调用方无法据以行动的 40 项 table_of_contents。
    result = recover_scanned_pdf(result, body, pages=pages, password=password)

    if not result.content_ok:
        return "", f"PDF extraction failed: {result.error or 'no extractable content'}", {}

    # OCR 跑过而一个字都没认出来：content 里只有占位符
    # "[No text detected on this page.]"，那不是可引用的正文。web_read 对同一批
    # 字节日判 pdf_no_text，本地这条腿从前却把它当成功交回、还带 quality_score=0.9
    # （2026-10-28 真机 p16）—— 两条门对同一份文件必须给出同一份判决。
    from dhole_mcp.quality import pdf_text_is_absent
    if pdf_text_is_absent(result.content):
        return "", (
            "pdf_no_text: scanned/image PDF with no extractable text layer "
            "(OCR found nothing)"), {
            "way_forward": (
                "OCR ran on this file and read no text, so its pages are images "
                "rather than text. Re-sending the same file cannot change that; "
                "read a text-layer copy of the document instead."),
        }

    extras = {
        "table_of_contents": result.table_of_contents or [],
        "metadata": result.metadata or {},
        "pages_total": result.pages_total,
        "pages_returned": len(result.pages_extracted),
        "quality_score": result.quality_score,
        # 抽取器的判决，不是猜的：quality_score 一旦有值，_agent_hints 就听它的，所以
        # 一份健康 PDF 若在这里报 content_ok=False，会被标成 "do not cite"。
        "content_ok": bool(result.content_ok),
    }

    # 抽取器的正文已经以 `# <title>` 开头（作者在它的 metadata 头里）—— `_format_metadata`
    # 把它们放在那儿，`ocr_pdf` 也一样。再前置一遍会让每一份有标题的本地 PDF 把标题印
    # **两遍**，而同一批字节走 HTTP 只印一遍：两条路对同一份文档各说一套，而这正是这颗
    # 函数的 docstring 承诺不会发生的事。标题与作者仍然在 `extras["metadata"]` 里，
    # 想要字段形式的调用方拿得到。
    parts: list[str] = ["\n\n".join(result.content)]

    # 只在有东西不对时才注记 —— 干净的 PDF 不加噪声。
    notes: list[str] = []
    if result.ocr_fallback_used:
        notes.append("OCR fallback used (scanned or CID-garbled pages)")
    if result.quality_score and result.quality_score < 0.6:
        notes.append(f"low extraction quality ({result.quality_score:.2f})")
    if notes:
        parts.append("> " + "; ".join(notes))

    return "\n\n".join(p for p in parts if p).strip(), "", extras


def _table_to_markdown(table) -> str:
    """把 python-docx 的 Table 转成 Markdown 表格字符串。"""
    rows_data: list[list[str]] = []
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        rows_data.append(cells)

    if not rows_data:
        return ""

    col_count = max(len(r) for r in rows_data)
    # 把各行补齐到同一长度
    for r in rows_data:
        while len(r) < col_count:
            r.append("")

    header_str = "| " + " | ".join(rows_data[0]) + " |"
    sep_str = "| " + " | ".join("---" for _ in range(col_count)) + " |"
    data_strs = ["| " + " | ".join(r) + " |" for r in rows_data[1:]]

    return "\n".join([header_str, sep_str] + data_strs)
