"""Byte-to-text decoding: one place that decides what a body's charset is.

Two callers, two situations, one shared vocabulary for "this text looks wrong":

* HTTP (``fetcher._decode_html_bytes``) starts from a Content-Type charset -
  but the header can lie, so the bytes get a vote.
* Local files (``parse``) have no header at all: only the bytes, an optional
  BOM, and whatever the caller declares.

Both need the same question answered - "which of these decodes is least
damaged?" - so the markers and the scoring live here instead of in either
caller.

Deliberately imports nothing from dhole_mcp: parse.py promises to work without
the fetch stack (no primp), and this is the module it needs when the [all]
extra is not installed.
"""

from __future__ import annotations

import codecs

# Prefixes a UTF-8 multibyte sequence leaves behind when decoded as latin-1 /
# cp1252: U+00C2/U+00C3/U+00E2 followed by more bytes, and the stray BOM.
_MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â\x80", "â\x9d", "ï»¿")

_PUA_START = 0xE000
_PUA_END = 0xF8FF


def misdecode_score(text: str) -> int:
    """How damaged a decode looks, ignoring private-use characters.

    Replacement chars plus mojibake markers. Lower is better; 0 means clean.
    This is the score the HTTP path compares two candidates with.
    """
    if not text:
        return 0
    score = text.count("\ufffd")
    for marker in _MOJIBAKE_MARKERS:
        score += text.count(marker)
    return score


def decode_damage(text: str) -> int:
    """misdecode_score plus private-use-area characters.

    The PUA term lives here and not in misdecode_score because it only carries
    information when GB18030 is one of the candidates: GB18030 maps a large
    number of byte pairs straight into the PUA, so a Big5 document decoded as
    GB18030 puts 5-6 characters per 30 into it (measured on 4 samples), while a
    genuine GBK document puts none in (measured on 7 samples, worst case 0).
    The HTTP path never tries GB18030, so the term would only add noise there.
    """
    score = misdecode_score(text)
    for ch in text:
        if _PUA_START <= ord(ch) <= _PUA_END:
            score += 1
    return score


# ─── Local-file decoding ──────────────────────────────────────────────────────
#
# A local file carries no header to declare its charset. Candidate order:
#
#   1. BOM. The file states its own encoding, and a BOM is never accidental.
#   2. An explicit caller declaration. A hint, not a command: a name that does
#      not decode strictly falls through to detection, and the returned
#      encoding name says which one actually won.
#   3. A NUL byte in the head. A UTF-16/32 body whose BOM was stripped decodes
#      "cleanly" as GB18030 into text full of NULs - damage 0, content garbage.
#   4. Strict UTF-8. A byte stream that decodes as UTF-8 essentially never came
#      out of a legacy encoder, so this is a safe "is it UTF-8?" test.
#   5. Strict GB18030, accepted only when it comes out clean. A superset of GBK
#      and GB2312, so one candidate covers the whole family - measured clean on
#      7/7 GBK samples.
#   6. A scored comparison between utf-8 and gb18030, keeping whichever leaves
#      the least damage.
#
# Known boundary, measured rather than assumed: GB18030 decodes the *other*
# CJK legacy encodings strictly too, so Shift_JIS ("名前" -> "柤慜") and EUC-KR
# come back as wrong-but-plausible Chinese with damage 0. Big5 is the
# exception - it lands in the PUA and is flagged. Nothing cheap separates the
# first two, so pass encoding= for them.
_BOM_ENCODINGS = (
    # UTF-32-LE's BOM begins with UTF-16-LE's, so it has to be tested first.
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# Tried strictly, in order, when there is no BOM and no usable declaration.
# Deliberately short. big5/shift_jis/euc-kr would decode GBK bytes into wrong
# characters, and cp1252 decodes *everything* into zero-damage Latin text,
# which would make the damage signal worthless - a wrong answer that looks
# clean is worse than a flagged one.
_AUTO_ENCODINGS = ("utf-8", "gb18030")

# Scored fallback: every candidate is lossy for this file, so the least
# damaged wins. cp1252 is absent for the reason above.
_SCORED_ENCODINGS = ("utf-8", "gb18030")


def decode_file_bytes(body: bytes, declared: str = "") -> tuple[str, str, int]:
    """Decode local-file bytes. Returns ``(text, encoding_used, damage)``.

    ``damage`` counts the replacement chars, mojibake markers and PUA
    characters left in the chosen decode - 0 means the text is clean. Never
    raises: a caller always gets text, an encoding name and a damage score, so
    it can report an unreliable decode instead of having to infer it from the
    characters themselves.
    """
    for bom, enc in _BOM_ENCODINGS:
        if body.startswith(bom):
            try:
                return body.decode(enc), enc, 0
            except (LookupError, UnicodeError):  # pragma: no cover - BOM implies valid
                break

    if declared:
        try:
            return body.decode(declared), declared, 0
        except (LookupError, UnicodeError):
            # Unknown or wrong name: not a usable instruction, so detect
            # instead of returning replacement soup.
            pass

    if b"\x00" in body[:64]:
        for enc in ("utf-16", "utf-32"):
            try:
                return body.decode(enc), enc, 0
            except (LookupError, UnicodeError):
                continue

    for enc in _AUTO_ENCODINGS:
        try:
            text = body.decode(enc)
        except UnicodeDecodeError:
            continue
        if decode_damage(text) == 0:
            return text, enc, 0
        # Decoded strictly but came out damaged (GB18030 does this to Big5).
        # Fall through to the scored comparison instead of returning it as a
        # success - the caller needs the damage number either way.
        break

    best: tuple[str, str, int] | None = None
    for enc in _SCORED_ENCODINGS:
        try:
            text = body.decode(enc, errors="replace")
        except LookupError:  # pragma: no cover - both are stdlib codecs
            continue
        damage = decode_damage(text)
        if best is None or damage < best[2]:
            best = (text, enc, damage)
    return best if best else ("", "utf-8", 0)
