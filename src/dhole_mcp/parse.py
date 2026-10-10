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


def _cfb_refusal(ext: str) -> str:
    """The refusal for an OLE2/CFB file, named for what it actually is.

    Both shapes this replaces were worse. A `.doc` was answered with "rename it
    to .txt", which cannot work - renaming does not change the bytes - and a CFB
    file renamed to `.docx` (a very common attempt) fell through to python-docx
    and came back as ``Parse error (.docx): PackageNotFoundError: Package not
    found at '...'``, an internal exception naming neither the real format nor
    any way forward. Both measured.
    """
    entry = _CFB_LEGACY.get(ext)
    if entry:
        head, target = entry
    else:
        target = "docx"
        head = ("This file starts with the OLE2/CFB signature: a pre-2007 "
                "Office document, or another CFB container (msi, msg, ...) - "
                "not a document format this reader opens")
    return (f"{head}. There is no text layer a reader can walk here, and "
            f"renaming it does not change the bytes. Convert it first - "
            f"`soffice --headless --convert-to {target} <file>` (LibreOffice), "
            f"or the authoring app's 'Save As .{target}' - then parse the "
            f"result.")


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


def _decode_body(body: bytes, declared: str = "") -> tuple[str, dict]:
    """Decode file bytes and package the envelope fields. See charset.decode_file_bytes."""
    from dhole_mcp.charset import decode_file_bytes, encoding_was_guessed

    text, used, damage = decode_file_bytes(body, declared)
    extras: dict = {"encoding": used}
    if damage:
        extras["decode_damage"] = damage
    if encoding_was_guessed(body, declared, used):
        # 这一颗不是"检测结果可疑"的意思，是"这个名字是猜出来的"：GB18030 严格解得开
        # Shift_JIS / EUC-KR 的字节（charset.py 上面记着这条实测边界），所以日/韩旧
        # 编码在这里出来的是**看着像中文的错字**，damage 0、content_ok true。能诚实说
        # 的只有 weaker 的那半句 —— 没有人声明过编码，而这一个是 dhole 选的。
        extras["encoding_guessed"] = True
    return text, extras


def _read_text_file(file_path: str, declared: str = "") -> tuple[str, dict]:
    """Read a local file as bytes and decode it. See charset.decode_file_bytes."""
    with open(file_path, "rb") as f:
        body = f.read()
    return _decode_body(body, declared)


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
    carry web_read's meaning; they are ignored for every other format, exactly
    as web_read ignores them on a non-PDF response.
    """
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
        return "", (
            f"File too large ({file_size / 1024 / 1024:.1f} MB). "
            f"Maximum supported size is {MAX_PARSE_FILE_SIZE // 1024 // 1024} MB."
        ), {}

    ext = os.path.splitext(file_path)[1].lower()

    # The bytes decide before the extension does. A CFB file is never a modern
    # Office document, so one check covers both a plain `.doc` and a `.docx`
    # that is really a renamed `.doc` - the two shapes that used to get a
    # useless answer (a rename hint that cannot work, and python-docx's
    # PackageNotFoundError).
    if _sniff_cfb(file_path):
        return "", _cfb_refusal(ext), {}

    if ext not in SUPPORTED_EXTENSIONS:
        # 报错点名支持集，再给一条下一步。只写「不支持」的话，agent 会以为这条路
        # 整个走不通而放弃读文件——而它手里那份 .log/.rst/.ini 多半就是文本。
        return "", (
            f"Unsupported file type '{ext}'. Supported: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}. If the file is plain "
            "text under another extension, copy or rename it to .txt (or .md) "
            "and parse that."
        ), {}

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
        from dhole_mcp import install as _install
        return "", (
            f"Missing dependency for {ext} parsing: {e}. "
            f"{_install.docx_install_advice()}"
        ), {}
    except Exception as e:
        return "", f"Parse error ({ext}): {type(e).__name__}: {str(e)[:200]}", {}

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
    html, extras = _decode_body(body, encoding or _html_declared_charset(body))
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
        if declared:
            # A partial id list must not cost a slide: whatever it named comes
            # first, in its order; anything it did not name follows by filename.
            names = declared + _pptx_filename_order(
                [n for n in names if n not in declared])
        else:
            names = _pptx_filename_order(names)
        lines = [f"[{len(names)} slide(s); text only - layout and diagram text "
                 "are not included]"]
        for n in names:
            xml = zf.read(n).decode("utf-8", "replace")
            lines.append(f"\n## {n.rsplit('/', 1)[-1]}")
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


def _parse_docx(file_path: str) -> str:
    """Parse .docx to Markdown: body paragraphs and tables, in document order.

    The first line names what a text reader cannot carry, the same contract
    .pptx and .odt follow (see _docx_dropped_parts).
    """
    from docx import Document

    doc = Document(file_path)
    lines: list[str] = []
    n_paras = 0
    n_tables = 0

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
        # Map heading styles to Markdown headings
        style = (block.style.name or "").lower()
        if "heading 1" in style or style == "title":
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
    head = (f"[Word document; {n_paras} paragraph(s), {n_tables} table(s) "
            f"read{tail}]")
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
            header_str = "| " + " | ".join(
                cell_text(header_idx, j, header[j] if j < len(header) else None)
                for j in range(col_count)) + " |"
            sep_str = "| " + " | ".join("---" for _ in range(col_count)) + " |"
            data_strs = []
            for row_idx, row in kept[1:]:
                cells = list(row) + [None] * (col_count - len(row))  # pad short rows
                data_strs.append("| " + " | ".join(
                    cell_text(row_idx, j, cells[j]) for j in range(col_count)) + " |")

            section = (f"## {sheet_name}\n\n"
                       + "\n".join([header_str, sep_str] + data_strs))

            notes: list[str] = []
            if uncached:
                notes.append(f"{len(uncached)} formula cell(s) had no cached value "
                             "and are shown as their formula")
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
