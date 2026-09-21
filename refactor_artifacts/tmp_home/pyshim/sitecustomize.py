"""Offline socket guard installed by refactor_artifacts/tools/mcp_snapshot.py.

Auto-loaded by CPython as ``sitecustomize`` because this directory is on
PYTHONPATH for the snapshot run. It refuses every connect() to a non-local
address and every getaddrinfo() for a non-local hostname, so the snapshot can
never make a real outbound internet request. Loopback / private / link-local
addresses are still allowed (they cannot leave the machine).
"""

import ipaddress
import socket

_LOCAL_HOSTNAMES = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})


def _is_local_host(host):
    if host is None:
        return True
    if isinstance(host, (bytes, bytearray)):
        try:
            host = bytes(host).decode("ascii", "replace")
        except Exception:
            return False
    text = str(host).strip()
    if not text:
        return True
    if text.lower() in _LOCAL_HOSTNAMES:
        return True
    bare = text.strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return False  # a name we cannot prove local -> treat as remote
    return not ip.is_global


_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex
_orig_getaddrinfo = socket.getaddrinfo


def _guarded_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
        if not _is_local_host(address[0]):
            raise OSError(10061, "dhole-snapshot-offline-guard: refused non-local connect to %r" % (address[0],))
    return _orig_connect(self, address)


def _guarded_connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
        if not _is_local_host(address[0]):
            return 10061  # no packet leaves the machine
    return _orig_connect_ex(self, address)


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    if not _is_local_host(host):
        raise socket.gaierror(-2, "dhole-snapshot-offline-guard: blocked DNS for %r" % (host,))
    return _orig_getaddrinfo(host, port, *args, **kwargs)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.getaddrinfo = _guarded_getaddrinfo
