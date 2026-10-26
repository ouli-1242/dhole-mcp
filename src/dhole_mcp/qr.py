"""为抓到的像素做本地 QR 解码（确定性工作，不是视觉）。

解 QR 码不是模型任务：格式是确定性的，本地解码器能精确回答。本模块存在的目的，
是让 agent 从页面上读出登录二维码 / 签到 URL / token——结果走文本通道，任何
图像都不需要被"看见"。

解码器是 OpenCV 的 QRCodeDetector（``opencv-python-headless``，一个 ``[all]``
extra），像其它重的可选依赖一样惰性 import（ocr.py 的模式）：轻装安装保持轻，
缺失的依赖连同安装命令一起点名，而不是冒出一个裸的 ``ImportError: cv2``。
"""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger("dhole-mcp.qr")

# 超过这个大小的抓图按错误处理，而不是拿来解码的图片：agent 本意是裁 QR 元素，
# 不是整页。截图路径在尝试解码之前检查。
MAX_DECODE_BYTES = 8_000_000


def qr_available() -> bool:
    """OpenCV（QR 解码器）能不能 import。廉价探测，不加载。"""
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def _get_cv2() -> Any:
    try:
        import cv2  # type: ignore
        return cv2
    except ImportError as e:
        from dhole_mcp import install as _install
        raise ImportError(
            f"decode_qr requires OpenCV (opencv-python-headless). "
            f"Run: {_install.full_install_command()}"
        ) from e


def decode_qr_bytes(data: bytes) -> List[str]:
    """解码一张编码图片（PNG/JPEG/WebP 字节）里的每一个 QR 码。

    返回解码后的载荷字符串列表（一页可以带多个 QR）。顺序跟随检测器自己的扫描
    顺序。读不出 QR 的图片返回 []——那是一个答案（"这里没有可解码的东西"），
    不是错误：agent 可能裁错了元素，或码退化得太厉害，而像素仍然留给多模态
    agent。

    元素裁剪几乎从不带安静区（规范要求码周围留四个模块的边距；一张
    ``<img class="qr">`` 的截图正好停在码的边缘），所以直接扫不到时，会给裁剪
    补一圈白边并放大后重试——这两种归一化能修好真实裁剪，按这个顺序试。

    缺 OpenCV 时抛 ImportError（消息点名 extra），字节完全不是可解码图片时抛
    ValueError。
    """
    if not data:
        return []
    if len(data) > MAX_DECODE_BYTES:
        raise ValueError(
            f"decode_qr: capture is {len(data):,} bytes - crop a smaller "
            "element instead of decoding a full-page capture")
    cv2 = _get_cv2()
    import numpy as np
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(
            "decode_qr: capture is not a decodable image (the screenshot "
            "returned bytes OpenCV cannot parse)")

    def _scan(frame) -> List[str]:
        try:
            detector = cv2.QRCodeDetector()
            out = detector.detectAndDecodeMulti(frame)
            # OpenCV 4.7+ 返回 (retval, decoded_info, points[, straight_qrcode])；
            # 旧版本返回三个。按索引取，绝不解包——元数已经变过一次，这里解错
            # 会被读成"没找到 QR"。
            ok, payloads = bool(out[0]), out[1]
        except Exception as e:  # 一帧坏数据不能弄挂整次抓取
            logger.debug("QR detection failed: %s", e)
            return []
        if not ok or payloads is None:
            return []
        return [str(p) for p in payloads if p]

    found = _scan(img)
    if found:
        return found
    # 安静区修复：先给裁剪补白边（borderIsolated，让填充被读成背景而不是内容），
    # 再对模块在截图分辨率下只有几像素宽的码做放大。
    h, w = img.shape[:2]
    padded = cv2.copyMakeBorder(img, h // 4, h // 4, w // 4, w // 4,
                                cv2.BORDER_CONSTANT, value=(255, 255, 255))
    found = _scan(padded)
    if found:
        return found
    scale = max(1, 400 // max(h, w))
    if scale > 1:
        big = cv2.resize(img, (w * scale, h * scale),
                         interpolation=cv2.INTER_CUBIC)
        big = cv2.copyMakeBorder(big, h // 2, h // 2, w // 2, w // 2,
                                 cv2.BORDER_CONSTANT, value=(255, 255, 255))
        found = _scan(big)
    return found
