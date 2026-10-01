"""Capability-aware install hints (W6).

Two rules:

1. The string ``pip install dhole-mcp[all]`` is a claim about what the operator
   needs to run. W7 will make ``pip install dhole-mcp`` the full tier (with
   ``[all]`` retained as a compatibility alias for the self-heal path), which
   means every call site that today hard-codes the string has to change. It
   should not: ``full_install_command`` is the single place that knows, and
   W7 changes one function instead of 49 sites.

2. Telling someone to install what they already installed is a lie, and this
   project has already been bitten by it (see ``pdf_extractor.ocr_advice``'s
   docstring: "a live run lost its first hour to exactly that"). Each
   ``*_install_advice`` helper below first checks whether the tier is
   importable, and only emits the install string when it is genuinely absent.

W6 §3.1 also requires the hint appear at the *use point* rather than at
startup - ``print_capabilities`` keeps its line for ``-v``, but every tool
response that hits a missing tier gets its own hint via one of these helpers.
"""

from __future__ import annotations


def full_install_command() -> str:
    """The canonical "install every optional tier" command.

    Currently ``pip install dhole-mcp[all]``. W7's extras flip changes this
    line and nothing else - sites call the helper, not the string.
    """
    return "pip install dhole-mcp[all]"


def browser_install_advice() -> str:
    """Advice for a browser-tier failure. Only tells the user to install if
    the browser dependencies are genuinely missing on THIS machine.

    ``playwright install chromium`` stays a separate step - installing the
    Python packages does not download the browser binary, and pretending
    otherwise is how "pip install did not fix it" bug reports start.
    """
    try:
        # Local import to keep install.py at the bottom of the dependency
        # graph (server imports install, install must not import server).
        from dhole_mcp.server import _browser_deps_available
        if _browser_deps_available():
            return ("browser tier is installed - this call failed for another "
                    "reason (see the error above); a fresh `playwright install "
                    "chromium` also fixes a half-downloaded browser")
    except Exception:  # noqa: BLE001
        pass
    return (f"Install the browser tier with: {full_install_command()}, then "
            "`playwright install chromium`")


def ocr_install_advice() -> str:
    """The single source the four OCR sites already route through (see
    ``pdf_extractor.ocr_advice``). Kept here so W7's extras flip only touches
    one function; ``pdf_extractor.ocr_advice`` becomes a thin wrapper.
    """
    try:
        from dhole_mcp.ocr import ocr_available
        if ocr_available():
            return ("OCR is installed but recovered no text here - use a vision "
                    "tool / screenshot, or another source.")
    except Exception:  # noqa: BLE001
        pass
    return (f"Install OCR support with `{full_install_command()}` and dhole will "
            "auto-OCR it; or use a vision-capable tool / screenshot.")


def pdf_install_advice() -> str:
    """Advice for a PDF-extraction failure that looks like a missing dep."""
    try:
        import pdfplumber  # noqa: F401
        return ("pdfplumber is installed - the extraction failed for another "
                "reason (corrupt file, wrong password, unsupported encoding)")
    except Exception:  # noqa: BLE001
        return f"Install PDF support with: {full_install_command()}"


def docx_install_advice() -> str:
    """Advice for ``parse`` refusing a .docx because python-docx is not there."""
    try:
        import docx  # noqa: F401
        return ("python-docx is installed - the parse failure was not a missing "
                "dependency (corrupt file? wrong extension?)")
    except Exception:  # noqa: BLE001
        return f"Install docx support with: {full_install_command()}"


def qr_install_advice() -> str:
    """Advice for a QR decode failure caused by OpenCV not being there."""
    try:
        import cv2  # noqa: F401
        return ("opencv is installed - the QR decode failed for another reason "
                "(image too small / no QR in frame)")
    except Exception:  # noqa: BLE001
        return f"Install QR decoding with: {full_install_command()}"


def rerank_install_advice() -> str:
    """Advice for the neural reranker tier. Model download is a separate step
    (``dhole model use``); the hint names both."""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return ("reranker packages are installed - the failure was model state, "
                "not deps; try `dhole model use <name>` to reselect")
    except Exception:  # noqa: BLE001
        return (f"Install the neural rerank tier with: {full_install_command()}; "
                "the model (~279MB for bge-zh) is fetched on first use")
