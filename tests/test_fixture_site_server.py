"""靶站的服务器得是「静音」的那个 —— 否则判据被噪音污染。

上一次 `evals/fixture_site.py` 跑完留下的输出里，stdout 是约 40 条成功的请求
（全 200，外加预期内的 `POST /login -> 400` 与 `-> 302`），stderr 却是 6 条
`ConnectionResetError [WinError 10054]` 的整段 traceback。当时的第一反应是
「靶站失败了」，查了一圈才发现是隐身浏览器收尾时掐断连接——**每个请求都成功**。

这正是本项目最忌讳的失败形状，只是方向反过来：不是「看起来成功、诊断自信地
错」，而是「看起来失败、实际全过」。代价一样——真故障会淹在假故障里，而这个
靶站是 `evals/` 全部判据的来源。

所以这里钉两件事：①「对端先走」这一类只打一行、不打栈；②其余异常照旧打全栈，
不许顺手把 `handle_error` 整个吞掉（静默一切会让真故障变成「没有输出」）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import fixture_site as fs  # noqa: E402

CLIENT_GONE = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


@pytest.fixture
def server():
    """绑 0 号端口，只借实例来调 `handle_error`，不真的 serve。"""
    srv = fs.make_server("127.0.0.1", 0)
    try:
        yield srv
    finally:
        srv.server_close()


def _raise_through(server, exc: BaseException, address=("127.0.0.1", 51234)) -> None:
    """在 except 块里调 `handle_error` —— 它读 `sys.exc_info()`，不能凭空构造。"""
    try:
        raise exc
    except type(exc):
        server.handle_error(None, address)


class TestAClientGoingAwayIsNotAFailure:
    @pytest.mark.parametrize("exc_type", CLIENT_GONE)
    def test_it_prints_a_note_instead_of_a_traceback(self, server, capsys, exc_type):
        _raise_through(server, exc_type())
        captured = capsys.readouterr()

        assert "Traceback" not in captured.err, (
            f"{exc_type.__name__} 不该打成 traceback —— 那是浏览器的正常收尾")
        assert "client gone" in captured.out, "压掉噪音不等于压掉可见性"
        assert exc_type.__name__ in captured.out, "得说清是哪一种断法"
        assert "127.0.0.1:51234" in captured.out, "得说清是哪个对端"

    def test_the_note_goes_to_stdout_where_the_request_log_lives(self, server, capsys):
        """请求日志走 stdout，这一行也走 stdout —— 不然读者得同时看两路才能拼出
        「哪些请求成功了、哪个客户端走了」。"""
        _raise_through(server, ConnectionResetError())
        captured = capsys.readouterr()
        assert "client gone" in captured.out
        assert captured.err == ""


class TestEverythingElseStillPrintsTheFullStack:
    def test_a_real_bug_is_not_swallowed(self, server, capsys):
        """静默一切是本末倒置：真故障必须照旧可见，且带全栈。"""
        _raise_through(server, ValueError("靶站自己写错了"))
        captured = capsys.readouterr()
        assert "Traceback" in captured.err
        assert "ValueError" in captured.err
        assert "client gone" not in captured.out

    def test_an_unrelated_oserror_is_not_mistaken_for_a_disconnect(self, server, capsys):
        """`OSError` 是那三个的父类 —— 按父类判会把「端口被占」这类真故障一起压掉。"""
        _raise_through(server, OSError(22, "not a disconnect"))
        captured = capsys.readouterr()
        assert "Traceback" in captured.err, "只认三个具体类型，不认 OSError"


class TestTheServerMainActuallyStartsIsTheQuietOne:
    def test_the_factory_returns_the_quiet_subclass(self, server):
        assert isinstance(server, fs.QuietThreadingHTTPServer)
        assert fs.QuietThreadingHTTPServer is not fs.ThreadingHTTPServer

    def test_it_is_still_a_threading_server(self, server):
        """别为了静音把并发丢了 —— 靶站要同时服务浏览器与 dhole 的并发请求。"""
        from http.server import ThreadingHTTPServer

        assert isinstance(server, ThreadingHTTPServer)
        assert server.daemon_threads is True

    def test_it_serves_the_fixture_handler(self, server):
        from http.server import BaseHTTPRequestHandler

        assert issubclass(server.RequestHandlerClass, BaseHTTPRequestHandler)
        assert server.RequestHandlerClass is fs.Handler
