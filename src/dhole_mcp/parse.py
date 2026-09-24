"""Local file parsing for Dhole MCP.

Converts local files (.html, .htm, .xhtml, .docx, .xlsx, .csv, .pdf) to Markdown
so agents can read documents without a web fetch. Part of the [all] extra
(python-docx, openpyxl). Graceful degradation: if deps are missing, returns a
clear error.

Supported formats:
- .html / .htm / .xhtml → trafilatura + markdownify (reuses existing extraction)
- .docx         → python-docx: headings, paragraphs, tables → Markdown
- .xlsx         → openpyxl: sheets → Markdown tables
- .csv          → stdlib csv: → Markdown table
- .pdf          → pdf_extractor (same pipeline smart_fetch uses for PDF URLs:
                  layout-aware markdown, OCR fallback for scans, quality score)
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re

logger = logging.getLogger("dhole_mcp.parse")

# Supported extensions
SUPPORTED_EXTENSIONS = {".html", ".htm", ".xhtml", ".docx", ".xlsx", ".csv", ".pdf"}


# Maximum file size for parsing (50 MB) to prevent OOM on huge files
MAX_PARSE_FILE_SIZE = 50 * 1024 * 1024


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
    from dhole_mcp.charset import decode_file_bytes

    text, used, damage = decode_file_bytes(body, declared)
    extras: dict = {"encoding": used}
    if damage:
        extras["decode_damage"] = damage
    return text, extras


def _read_text_file(file_path: str, declared: str = "") -> tuple[str, dict]:
    """Read a local file as bytes and decode it. See charset.decode_file_bytes."""
    with open(file_path, "rb") as f:
        body = f.read()
    return _decode_body(body, declared)


def parse_file(file_path: str, encoding: str = "") -> tuple[str, str]:
    """Parse a local file to Markdown.

    Returns (content: str, error: str). On success error is empty.
    On failure content is empty and error explains what went wrong.
    Never raises.
    """
    content, error, _extras = parse_file_detailed(file_path, encoding)
    return content, error


def parse_file_detailed(file_path: str, encoding: str = "") -> tuple[str, str, dict]:
    """Parse a local file to Markdown, plus the envelope fields it can fill.

    Returns (content, error, extras). For PDFs ``extras`` carries
    table_of_contents / metadata / quality_score / content_ok - a local PDF
    should report the same envelope as one fetched by URL, and used to report
    toc=[] and metadata={} while the URL path reported both. For .html/.csv it
    carries ``encoding`` (the charset the bytes were decoded as) and, when the
    text came out damaged, ``decode_damage``. Empty for the other formats.
    Never raises.

    ``encoding`` overrides auto-detection; leave it empty unless the caller
    knows better than the bytes do.
    """
    if not file_path:
        return "", "file_path is required", {}

    # Expand ~ and resolve to absolute
    file_path = os.path.expanduser(file_path)
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(file_path)

    if not os.path.exists(file_path):
        return "", f"File not found: {file_path}", {}

    # Guard against OOM: reject files larger than 50 MB
    file_size = os.path.getsize(file_path)
    if file_size > MAX_PARSE_FILE_SIZE:
        return "", (
            f"File too large ({file_size / 1024 / 1024:.1f} MB). "
            f"Maximum supported size is {MAX_PARSE_FILE_SIZE // 1024 // 1024} MB."
        ), {}

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        # 纯文本是唯一「不该来这里」的一类：它不需要转换，agent 自己就能读。
        # 报错只列支持集的话，agent 会以为这条路走不通而放弃读文件；所以点名
        # 下一步。刻意不维护「哪些扩展名算纯文本」的清单 —— 任何清单都会漏，
        # 而漏掉的正是最需要这句话的那次。
        return "", (
            f"Unsupported file type '{ext}'. Supported: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}. "
            f"Plain text (.txt/.md/.log/source/config/data) needs no conversion - "
            f"read it directly with your own file tool instead of parse."
        ), {}

    try:
        if ext in (".html", ".htm", ".xhtml"):
            content, extras = _parse_html(file_path, encoding)
            return content, "", extras
        elif ext == ".docx":
            return _parse_docx(file_path), "", {}
        elif ext == ".xlsx":
            return _parse_xlsx(file_path), "", {}
        elif ext == ".csv":
            content, extras = _parse_csv(file_path, encoding)
            return content, "", extras
        elif ext == ".pdf":
            return _parse_pdf(file_path)
    except ImportError as e:
        return "", (
            f"Missing dependency for {ext} parsing: {e}. "
            f"Install with: pip install dhole-mcp[all]"
        ), {}
    except Exception as e:
        return "", f"Parse error ({ext}): {type(e).__name__}: {str(e)[:200]}", {}

    return "", f"Unsupported format: {ext}", {}


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


def _parse_docx(file_path: str) -> str:
    """Parse .docx to Markdown using python-docx."""
    from docx import Document

    doc = Document(file_path)
    lines: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            lines.append("")
            continue
        # Map heading styles to Markdown headings
        style = (para.style.name or "").lower()
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

    # Extract tables
    for table in doc.tables:
        lines.append("")
        lines.append(_table_to_markdown(table))
        lines.append("")

    return "\n".join(lines).strip()


def _parse_xlsx(file_path: str) -> str:
    """Parse .xlsx to Markdown tables using openpyxl."""
    from openpyxl import load_workbook

    wb = load_workbook(file_path, read_only=True, data_only=True)
    sections: list[str] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        # Stream rows with a cap to prevent OOM on huge sheets
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 200:  # header + 100 data + margin
                break
            rows.append(row)
        if not rows:
            continue
        # Filter out completely empty rows
        rows = [r for r in rows if any(cell is not None for cell in r)]
        if not rows:
            continue

        section = f"## {sheet_name}\n\n"
        # Convert to markdown table
        header = rows[0]
        col_count = len(header)
        header_str = "| " + " | ".join(str(c) if c is not None else "" for c in header) + " |"
        sep_str = "| " + " | ".join("---" for _ in range(col_count)) + " |"
        data_strs = []
        for row in rows[1:100]:  # cap at 100 data rows
            cells = list(row) + [None] * (col_count - len(row))  # pad short rows
            data_strs.append("| " + " | ".join(str(c) if c is not None else "" for c in cells[:col_count]) + " |")

        section += "\n".join([header_str, sep_str] + data_strs)
        if len(rows) > 101:
            section += f"\n\n... ({len(rows) - 101} more rows shown, sheet may have more)"
        sections.append(section)

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


def _parse_pdf(file_path: str) -> tuple[str, str, dict]:
    """Parse a local .pdf to Markdown, reusing the URL path's extractor.

    Same pipeline smart_fetch uses for PDF URLs - layout-aware markdown,
    per-page CID-garbage OCR fallback - so a local file behaves identically to a
    fetched one, envelope included: the extractor's table_of_contents,
    metadata and quality_score are handed back to the caller instead of being
    dropped here.

    Why not just hand it to smart_fetch as ``file://``: that tool's URL
    validator is an SSRF guard and rejects non-http(s) schemes by design.
    Reading a path the caller supplied is a different trust boundary - the same
    one this module already crosses for .docx/.xlsx/.csv/.html.

    Returns (content, error, extras) rather than just content, because a PDF
    can fail in ways the other formats cannot (encrypted, scanned with no text
    layer) and because it carries an envelope of its own.
    """
    from dhole_mcp.pdf_extractor import extract_pdf

    with open(file_path, "rb") as fh:
        body = fh.read()

    result = extract_pdf(body, extraction_type="markdown")

    if not result.content_ok:
        return "", f"PDF extraction failed: {result.error or 'no extractable content'}", {}

    extras = {
        "table_of_contents": result.table_of_contents or [],
        "metadata": result.metadata or {},
        "quality_score": result.quality_score or 0.0,
        # The extractor's own verdict, not a guess: _agent_hints defers to it
        # once quality_score is set, so a healthy PDF that reported
        # content_ok=False here would be flagged "do not cite".
        "content_ok": bool(result.content_ok),
    }

    parts: list[str] = []
    if result.title:
        parts.append(f"# {result.title}")
    if result.author:
        parts.append(f"*{result.author}*")
    parts.append("\n\n".join(result.content))

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
