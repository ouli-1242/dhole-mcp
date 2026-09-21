"""Find comments/docstrings that reference identifiers which no longer exist.

Why: this repo has zero TODO/FIXME markers, so the usual "stale comment" signal is
absent. The remaining failure mode is a comment that describes a symbol that was
renamed or deleted -- e.g. a comment saying "see _fallback_http_get" when that
function is gone. Those are exactly the comments a behavior-preserving refactor is
allowed (and expected) to correct, and they are mechanically detectable:

  1. Build the universe of every identifier that EXISTS anywhere in src/dhole_mcp:
     every assignment target, function/class/arg name, import alias, attribute name,
     and every string constant (so that env-var names and log keys count as "exists").
  2. Walk every comment (via tokenize) and every docstring (via ast), and pull out
     "code-shaped" words: they must contain an underscore, or be ALL_CAPS, or be
     immediately followed by "(".
  3. Report references that are not in the universe, with file, line, the comment
     text, and the extracted word.

Only code-shaped words are extracted, which is what keeps the false-positive rate
low: prose words like "the" or "cache" are not reported, but "_fallback_http_get",
"DHOLE_WORKDIR", "migrate_legacy_cache_dir" and "CrawlResponseModel()" are.

This is READ-ONLY analysis. It asserts nothing; it produces a worklist to verify by hand.
"""

from __future__ import annotations

import ast
import re
import tokenize
from pathlib import Path

SRC = Path("src/dhole_mcp")

CODE_SHAPED = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\b")
ALL_CAPS = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
FUNC_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Words that are code-shaped but are language/builtin/typing vocabulary, not symbols.
STOPWORDS = {
    "str", "int", "bool", "float", "dict", "list", "set", "tuple", "None", "True", "False",
    "Exception", "Error", "async", "await", "yield", "return", "raise", "import", "from",
    "self", "cls", "args", "kwargs", "len", "max", "min", "sum", "any", "all", "isinstance",
    "DNS", "HTTP", "HTTPS", "HTML", "JSON", "URL", "URLS", "API", "TLS", "SSRF", "UA", "OK",
    "HEAD", "GET", "POST", "ID", "IDs", "PDF", "OCR", "RSS", "ATOM", "SERP", "CSS", "JS",
    "SPA", "SPAS", "SERP", "AI", "LLM", "MCP", "CLI", "TTL", "CDN", "IP", "IPV", "TTFB",
    "QPS", "RPS", "MS", "KB", "MB", "GB", "UTF", "UTF_8", "ASCII", "OS", "CPU", "IO",
    "STDIO", "SSE", "BM25", "RBAC", "CORS", "XSS", "AB", "V", "W", "N", "S", "E",
    "NOT_A", "IS", "THE", "AND", "OR", "IF", "IN", "OF", "TO", "AT", "BY", "ON", "FOR",
    "AUTO", "ALL", "NONE", "TRUE", "FALSE", "NULL", "BITS", "BYTES", "CHARS", "LINES",
    "FIR", "REAL", "E2E", "JSON", "XML", "CSV", "DOCX", "XLSX", "PNG", "JPEG", "GIF",
}
# Common English words that happen to contain an underscore-ish shape or are ALL CAPS
# section labels. Extend if the scan proves noisy.
EXTRA_STOP = {"PER", "SEC", "MIN", "HOUR", "DAY", "WEEK", "MONTH", "YEAR", "MAX",
              "MIN_TTL", "_", "__", "___", "default", "value", "text", "data", "result"}


def collect_universe() -> tuple[set[str], set[str]]:
    """Return (identifiers, string_literals) found anywhere in the package."""
    idents: set[str] = set()
    strings: set[str] = set()
    for p in SRC.glob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                idents.add(n.id)
            elif isinstance(n, ast.Attribute):
                idents.add(n.attr)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                idents.add(n.name)
                for a in getattr(n, "args", []).args if hasattr(n, "args") else []:
                    idents.add(a.arg)
            elif isinstance(n, ast.arg):
                idents.add(n.arg)
            elif isinstance(n, ast.keyword) and n.arg:
                idents.add(n.arg)
            elif isinstance(n, ast.alias):
                idents.add((n.asname or n.name).split(".")[0])
                idents.add(n.name.split(".")[-1])
            elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                strings.add(n.value)
            elif isinstance(n, ast.Global):
                idents.update(n.names)
    # any identifier that appears inside any string literal also "exists"
    for s in list(strings):
        idents.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", s))
    return idents, strings


def iter_comments(path: Path):
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type == tokenize.COMMENT:
                yield tok.start[0], tok.string


def iter_docstrings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(n, clean=False)
            if doc:
                yield getattr(n, "lineno", 1), doc


def candidates(text: str) -> set[str]:
    found: set[str] = set()
    for w in CODE_SHAPED.findall(text):
        if "_" in w or ALL_CAPS.match(w):
            found.add(w)
    found.update(FUNC_CALL.findall(text))
    return found


def main() -> None:
    idents, strings = collect_universe()
    blamed: list[tuple[str, int, str, str]] = []
    for p in sorted(SRC.glob("*.py")):
        for lineno, text in list(iter_comments(p)) + list(iter_docstrings(p)):
            for w in sorted(candidates(text)):
                if w in STOPWORDS or w in EXTRA_STOP or w in idents:
                    continue
                if any(w in s for s in strings):  # mentioned in some string somewhere
                    continue
                blamed.append((p.name, lineno, w, " ".join(text.split())[:150]))

    print(f"# comment/docstring references to identifiers not found in src/ ({len(blamed)} hits)\n")
    for name, lineno, word, snippet in blamed:
        print(f"{name}:{lineno}\t{word}\t{snippet}")


if __name__ == "__main__":
    main()
