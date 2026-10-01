"""Local QR decoding for captured pixels (deterministic work, not vision).

Decoding a QR code is not a model task: the format is deterministic and a
local decoder answers it exactly. This module exists so a TEXT-ONLY agent can
still read a login QR / check-in URL / token off a page — the result travels
over the text channel, no image ever has to be seen. It is deliberately NOT
gated behind ``DHOLE_MULTIMODAL``: the flag declares "the calling agent has
eyes"; decoding QRs needs none.

The decoder is OpenCV's QRCodeDetector (``opencv-python-headless``, an ``[all]``
extra), imported lazily like every other heavy optional dep (ocr.py's pattern):
a lean install stays lean, and the missing dependency is named with its install
command instead of surfacing as a bare ``ImportError: cv2``.
"""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger("dhole-mcp.qr")

# Above this a capture is treated as a mistake, not an image to decode: the
# agent meant to crop the QR element, not the whole page. Checked by the
# screenshot paths before decode is attempted.
MAX_DECODE_BYTES = 8_000_000


def qr_available() -> bool:
    """True if OpenCV (the QR decoder) is importable. Cheap probe, no load."""
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
    """Decode every QR code in an encoded image (PNG/JPEG/WebP bytes).

    Returns the decoded payloads as a list of strings (a page can carry more
    than one QR). Order follows the detector's own scan order. An image with
    no readable QR returns [] — that is an answer ("nothing decodable here"),
    not an error: the agent may have cropped the wrong element or the code may
    be too degraded, and the pixels are still there for a multimodal agent.

    An ELEMENT CROP almost never carries a quiet zone (the spec asks for four
    modules of margin around the code; a screenshot of ``<img class="qr">``
    ends exactly at the code's edge), so when the direct pass finds nothing the
    capture is retried with a white border added and upscaled — the two
    normalizations that fix real crops, tried in that order.

    Raises ImportError when OpenCV is missing (message names the extra), and
    ValueError when the bytes are not a decodable image at all.
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
            # OpenCV 4.7+ returns (retval, decoded_info, points[, straight_qrcode]);
            # older builds return three. Index, never unpack — the arity moved
            # once already and a wrong unpack here reads as "no QR found".
            ok, payloads = bool(out[0]), out[1]
        except Exception as e:  # a malformed frame must not kill the fetch
            logger.debug("QR detection failed: %s", e)
            return []
        if not ok or payloads is None:
            return []
        return [str(p) for p in payloads if p]

    found = _scan(img)
    if found:
        return found
    # Quiet-zone repair: a white border around the crop (borderIsolated so the
    # padding reads as background, not as content), then an upscale for codes
    # whose modules are only a few pixels wide at screenshot resolution.
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
