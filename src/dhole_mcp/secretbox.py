"""Encryption at rest for credential state (G25 评审 H4).

What it protects: the cookie VALUES in ``sessions.db`` and every stored value in
``identity.db`` — but only when ``DHOLE_PERSIST_CREDENTIALS=1`` moved those
stores from memory onto disk. The default store is ``sqlite3.connect(":memory:")``,
and the file that never exists needs no cipher.

How: Windows DPAPI (``CryptProtectData``) called through ``ctypes`` — stdlib
only, zero new dependencies, and the key is bound to the Windows user account,
which is exactly the boundary that a copied, synced or backup-externalized file
loses (NTFS ACLs do not travel with the bytes). Stored form:
``dpapi:<base64 blob>`` in the same TEXT column that used to hold plaintext. A
value WITHOUT that prefix is legacy plaintext from a pre-17.1 file;
:func:`unprotect` passes it through so the stores' one-time startup migration
can read and re-encrypt it.

The deliberate limitation, said out loud: on non-Windows there is no DPAPI, and
this module refuses to invent a weaker substitute — :func:`protect` raises and
the caller stays in memory. Never a plaintext fallback; the review's verdict
was "宁可拒存也不回退明文". What DPAPI does NOT defend against is equally
deliberate: any process already running as this Windows user can decrypt like
dhole does. The adversary here is the file leaving the machine, not the machine
already being owned.
"""

from __future__ import annotations

import base64
import ctypes
import sys

_PREFIX = "dpapi:"
# No UI prompts, no credential-rollover dialogs: a server decrypting a cookie
# mid-fetch must never pop a window.
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretStoreUnavailable(RuntimeError):
    """The platform has no credential encryptor; the caller must not write."""


def available() -> bool:
    """True where :func:`protect` can work. The stores check this before they
    would open a file, and stay in memory (with a warning) when it is False."""
    return sys.platform == "win32"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _crypt_win(data: bytes, *, encrypt: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, max(len(data), 1))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise SecretStoreUnavailable(
            f"Crypt{'Protect' if encrypt else 'Unprotect'}Data failed "
            f"(GetLastError={ctypes.GetLastError()})"
            + (" - was this file written by a different Windows user?"
               if not encrypt else ""))
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(value: str) -> str:
    """``"secret" -> "dpapi:<base64 DPAPI blob>"``. UTF-8 round-trips."""
    if not available():
        raise SecretStoreUnavailable(
            "credential persistence needs Windows DPAPI; this platform keeps "
            "credentials in memory instead of writing them unencrypted")
    blob = _crypt_win(value.encode("utf-8"), encrypt=True)
    return _PREFIX + base64.b64encode(blob).decode("ascii")


def unprotect(stored: str) -> str:
    """The inverse. A value without the ``dpapi:`` prefix is legacy plaintext
    from a pre-17.1 file and comes back unchanged — the stores migrate those
    at startup, so the passthrough exists for the migration to have something
    to read, not as a storage format."""
    if not stored.startswith(_PREFIX):
        return stored
    if not available():
        raise SecretStoreUnavailable(
            "found a dpapi: value but this platform has no DPAPI")
    return _crypt_win(base64.b64decode(stored[len(_PREFIX):]),
                      encrypt=False).decode("utf-8")
