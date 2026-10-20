"""A cut model download must keep what it has.

Measured live on the mmarco 119MB file: the CDN delivered 1.7MB and closed. The
old code deleted the partial on every short read, so each retry (and each fresh
process) started from zero — the opposite of the "resumable" promise on the
wire. These tests pin the three shapes:

  * short read -> .part stays, destination does not appear;
  * server ignores Range (200 + full body) -> truncate first, never append;
  * server honors Range (206) -> append the remaining bytes.
"""

from dhole_mcp import reranker


class _Resp:
    def __init__(self, chunks, headers, status=200):
        self._chunks = list(chunks)
        self.headers = headers
        self.status = status

    def read(self, _n):
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install(monkeypatch, resp):
    monkeypatch.setattr(reranker.urllib.request, "urlopen", lambda req, timeout: resp)
    # The idle-abort clock is irrelevant here; keep the loop moving.
    monkeypatch.setattr(reranker.time, "monotonic", lambda: 0.0)


def test_a_short_read_keeps_the_partial_for_the_next_attempt(tmp_path, monkeypatch):
    dest = tmp_path / "model.onnx"
    _install(monkeypatch, _Resp([b"x" * 40, b""], {"Content-Length": "100"}))
    assert reranker._download_file("https://example.invalid/model", dest) is False
    part = dest.with_suffix(dest.suffix + ".part")
    assert part.exists() and part.stat().st_size == 40
    assert not dest.exists(), "a short read is not a finished model"


def test_a_server_that_ignores_range_restarts_clean(tmp_path, monkeypatch):
    dest = tmp_path / "model.onnx"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"OLD" * 10)
    _install(monkeypatch, _Resp([b"HELLO"], {"Content-Length": "5"}, status=200))
    assert reranker._download_file("https://example.invalid/model", dest) is True
    assert dest.read_bytes() == b"HELLO", "a 200 response must replace, never append"


def test_a_server_that_honors_range_resumes(tmp_path, monkeypatch):
    dest = tmp_path / "model.onnx"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"OLD" * 10)
    _install(monkeypatch, _Resp([b"WORLD"], {"Content-Length": "5"}, status=206))
    assert reranker._download_file("https://example.invalid/model", dest) is True
    assert dest.read_bytes() == b"OLD" * 10 + b"WORLD"
