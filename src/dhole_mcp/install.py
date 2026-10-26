"""带能力感知的安装提示（W6）。

两条规则：

1. 字符串 ``pip install dhole-mcp[all]`` 是一条关于"运维需要装什么才能跑"的
   声明。W7 会把 ``pip install dhole-mcp`` 变成完整档（``[all]`` 保留为自愈
   路径的兼容别名），这意味着今天硬编码这个字符串的每个调用点都得改。它不该
   改：``full_install_command`` 是唯一知道它的地方，W7 只改一个函数，而不是
   49 处。

2. 让人去装他已经装好的东西是句谎话，而本项目已经被它咬过（见
   ``pdf_extractor.ocr_advice`` 的 docstring："a live run lost its first hour
   to exactly that"）。下面每个 ``*_install_advice`` 辅助函数都先检查该档能不能
   import，只有真的缺了才发安装串。

W6 §3.1 还要求提示出现在**使用点**而不是启动时——``print_capabilities`` 保留它
给 ``-v`` 的那一行，但每个撞上缺失档位的工具响应都通过这里某个辅助函数拿到
自己的提示。
"""

from __future__ import annotations


def full_install_command() -> str:
    """"装齐所有可选档"的规范命令。

    当前是 ``pip install dhole-mcp[all]``。W7 翻转 extras 时只改这一行，别的
    都不动——各处调用的是辅助函数，不是字符串。
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
    """浏览器档失败时的建议。只有这台机器**真的**缺浏览器依赖时才让用户装。

    17.0 B3：chromium 不再需要用户自己装——第一次用到浏览器档时 dhole 会同步装。
    所以这里只剩两种真话：Python 依赖层没装（补 pip），或者二进制那一步失败/
    还没跑完（给出同一条命令）。
    """
    cmd = browser_binary_command()
    try:
        # 函数内 import，让 install.py 留在依赖图底部（server 会 import
        # install，install 不能 import server）。
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
    """四个 OCR 站点已经统一走的那一个来源（见 ``pdf_extractor.ocr_advice``）。
    放在这里，好让 W7 翻转 extras 时只碰一个函数；``pdf_extractor.ocr_advice``
    变成一个薄包装。
    """
    try:
        from dhole_mcp.ocr import ocr_available
        if ocr_available():
            return ("OCR is installed but recovered no text here - use a vision "
                    "tool, a web_act screenshot action, or another source.")
    except Exception:  # noqa: BLE001
        pass
    return (f"Install OCR support with `{full_install_command()}` and dhole will "
            "auto-OCR it; or use a vision-capable tool / a web_act screenshot action.")


def pdf_install_advice() -> str:
    """PDF 抽取失败、看起来像缺依赖时给的建议。"""
    try:
        import pdfplumber  # noqa: F401
        return ("pdfplumber is installed - the extraction failed for another "
                "reason (corrupt file, wrong password, unsupported encoding)")
    except Exception:  # noqa: BLE001
        return f"Install PDF support with: {full_install_command()}"


def qr_install_advice() -> str:
    """QR 解码失败、原因是 OpenCV 不在时给的建议。"""
    try:
        import cv2  # noqa: F401
        return ("opencv is installed - the QR decode failed for another reason "
                "(image too small / no QR in frame)")
    except Exception:  # noqa: BLE001
        return f"Install QR decoding with: {full_install_command()}"


def rerank_install_advice() -> str:
    """神经重排档的建议。17.0：模型不是"选"来的——第一次神经搜索会自己把它
    下载完（可续传），没有 model 选择命令了。"""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return ("reranker packages are installed - the failure was model state, "
                "not deps; a plain web_search re-fetches the model (its default `mode` "
                "ranks with it, and web_search has no `focus_engine`), and "
                "~/.dhole/models holds whatever was already downloaded")
    except Exception:  # noqa: BLE001
        return (f"Install the neural rerank tier with: {full_install_command()}; "
                "the model (~279MB for bge-zh) is fetched on first use")
