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


def browser_binary_command() -> str:
    """装 chromium 二进制的那条命令 —— 唯一一处知道它的地方。

    17.0 B3 之前这句话印在 4 个文件里，而且写的是 `playwright install chromium`
    （装的是上游 playwright 的浏览器，patchright 的 driver 只认自己清单里那一版）。
    现在 browser.py 真的去装、doctor 报告装没装上、错误文案给出命令，三处都读这一个值。
    """
    return "python -m patchright install chromium"


def browser_install_advice() -> str:
    """Advice for a browser-tier failure. Only tells the user to install if
    the browser dependencies are genuinely missing on THIS machine.

    17.0 B3: chromium 不再需要用户自己去装 —— 第一次用到浏览器档时 dhole 同步装它。
    所以这里只剩两种真话：Python 依赖层没装（补 pip），或者二进制那一步失败/还没跑完
    （给出同一条命令）。
    """
    cmd = browser_binary_command()
    try:
        # Local import to keep install.py at the bottom of the dependency
        # graph (server imports install, install must not import server).
        from dhole_mcp.server import _browser_deps_available
        if _browser_deps_available():
            return ("browser tier is installed - dhole installs chromium on first "
                    "use, so this is either that install failing or still running: "
                    f"`{cmd}` (state: ~/.dhole/browser_install.json)")
    except Exception:  # noqa: BLE001
        pass
    return (f"Install the browser tier with: {full_install_command()}; chromium "
            f"itself is installed on first use, or run `{cmd}`")


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
    """Advice for ``doc_parse`` refusing a .docx because python-docx is not there."""
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
    """Advice for the neural reranker tier. 17.0: 模型不是"选"来的 —— 第一次神经
    搜索会自己把它下载完（可续传），没有 model 选择命令了。"""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return ("reranker packages are installed - the failure was model state, "
                "not deps; a neural web_search re-fetches the model, and "
                "~/.dhole/models holds whatever was already downloaded")
    except Exception:  # noqa: BLE001
        return (f"Install the neural rerank tier with: {full_install_command()}; "
                "the model (~279MB for bge-zh) is fetched on first use")
