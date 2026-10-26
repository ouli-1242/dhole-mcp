"""凭据状态的落盘加密（G25 评审 H4）。

它保护什么：``sessions.db`` 里的 cookie **值**，以及 ``identity.db`` 里每一颗
存储值——但只在 ``DHOLE_PERSIST_CREDENTIALS=1`` 把这两个存储从内存搬到磁盘之后。
默认存储是 ``sqlite3.connect(":memory:")``，一个从不存在的文件不需要密码。

怎么做：通过 ``ctypes`` 调 Windows DPAPI（``CryptProtectData``）——只用标准库、
零新依赖，密钥绑定 Windows 用户账户，而这正是被复制、同步或备份到外部的文件会
丢掉的那条边界（NTFS ACL 不跟着字节走）。存储形式：``dpapi:<base64 blob>``，
放在原来存明文的同一个 TEXT 列里。没有该前缀的值是 pre-17.1 文件的旧明文；
:func:`unprotect` 原样放行，好让两处存储的启动迁移能读到并重新加密它。

刻意说出口的限制：非 Windows 没有 DPAPI，本模块拒绝发明一个更弱的替代品——
:func:`protect` 抛异常，调用方留在内存。绝不回退明文；评审的判词是"宁可拒存
也不回退明文"。DPAPI 防不住什么同样是刻意的：任何已经以这个 Windows 用户身份
运行的进程都能像 dhole 一样解密。这里的对手是离开这台机器的文件，不是已经被
攻陷的机器。
"""

from __future__ import annotations

import base64
import ctypes
import sys

_PREFIX = "dpapi:"
# 不弹 UI、不弹凭据轮换对话框：一个在抓取途中解 cookie 的服务器绝不能弹窗。
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretStoreUnavailable(RuntimeError):
    """这个平台没有凭据加密器；调用方不得写盘。"""


def available() -> bool:
    """``protect`` 能工作的平台返回 True。两处存储在准备打开文件之前检查它；
    为 False 时留在内存（并告警）。"""
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
    """``"secret" -> "dpapi:<base64 DPAPI blob>"``。UTF-8 可往返。"""
    if not available():
        raise SecretStoreUnavailable(
            "credential persistence needs Windows DPAPI; this platform keeps "
            "credentials in memory instead of writing them unencrypted")
    blob = _crypt_win(value.encode("utf-8"), encrypt=True)
    return _PREFIX + base64.b64encode(blob).decode("ascii")


def unprotect(stored: str) -> str:
    """逆操作。没有 ``dpapi:`` 前缀的值是 pre-17.1 文件的旧明文，原样交回——
    两处存储在启动时迁移它们，所以这条放行是给迁移"有东西可读"，不是一种存储
    格式。"""
    if not stored.startswith(_PREFIX):
        return stored
    if not available():
        raise SecretStoreUnavailable(
            "found a dpapi: value but this platform has no DPAPI")
    return _crypt_win(base64.b64decode(stored[len(_PREFIX):]),
                      encrypt=False).decode("utf-8")
