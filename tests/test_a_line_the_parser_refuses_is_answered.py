"""一行 SDK 拒解析的 JSON，必须拿到 -32700，而不是永远的沉默（D7）。

症状（2026-10-09 实测）：往 stdio 送一行 JSON-RPC，只要它在**解析**阶段就失败
—— 参数嵌套超过解析器的递归上限（实测约 92 层起）、被截断的半截 JSON、或压根
不是 JSON —— 服务器**一个字节都不回**，stderr 也是空的，调用方就那么干等着。
JSON-RPC 2.0 对这种输入的规定是明确的：`-32700 Parse error`。报告原记的是
"200 层空响应 / 250 层无响应"，实测边界比它低得多（150 层也已经静默）。

根因不在 dhole 的处理器里：SDK 的 `stdin_reader` 用
`types.jsonrpc_message_adapter.validate_json` 校验每一行，失败时把异常**当作
一个流条目**塞进读流（类型签名就是 `ReadStream[SessionMessage | Exception]`）
然后继续读下一行，什么都不写回。dhole 自己的深度守卫
（`_validate_schema_selectors` 的 `MAX_SCHEMA_DEPTH = 8`）因此**根本轮不到出场**
—— 它要的是一条已经解析成消息的请求，而那条消息从来没被造出来。

修法在读流这一侧：`ReadStream` 是 Protocol，包一层即满足；回答经 SDK **自己的
写流**发出，所以 stdout 上始终只有一个写者（不与 `stdout_writer` 交错，不会撕
帧），而且用的就是 SDK 自己在 HTTP 传输层用的那句
`SessionMessage(JSONRPCError(...))`。

这一组钉三件事：①三种坏输入各拿一条 -32700（真起进程走真 stdio）；②坏输入之后
的正常调用照常应答 —— 会话没被搞坏、顺序没乱；③包装对象忠实实现了 ReadStream
协议（异常被答、正常条目原样通过、连续异常各答一条、`aclose` / `last_context`
如实转发、流结束原样抛出）。把 `serve()` 里那层包装摘掉，①②两类的真 stdio 用例
必须整片变红。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time

import anyio
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ─── 真 stdio：三种坏输入各拿一条 -32700 ────────────────────────────

class _Wire:
    """一台只用来问"这一行你回什么"的 dhole，按到达顺序记下每一行。"""

    def __init__(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("DHOLE_TOOLS", "DHOLE_USAGE_LOG")}
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dhole_mcp"], cwd=str(REPO_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines: list = []
        self.stderr_text = ""
        self._cond = threading.Condition()

        def _out():
            for raw in iter(self.proc.stdout.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                with self._cond:
                    self.lines.append(text)
                    self._cond.notify_all()

        def _err():
            chunks = []
            for raw in iter(self.proc.stderr.readline, b""):
                chunks.append(raw.decode("utf-8", "replace"))
            self.stderr_text = "".join(chunks)

        threading.Thread(target=_out, daemon=True).start()
        threading.Thread(target=_err, daemon=True).start()

    def send(self, obj):
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def send_raw(self, text):
        self.proc.stdin.write(text.encode("utf-8"))
        self.proc.stdin.flush()

    def wait_for(self, count, timeout=90):
        """等到达行数到 count（-32700 的 id 是 null，不能按 id 等）。"""
        deadline = time.time() + timeout
        with self._cond:
            while len(self.lines) < count:
                left = deadline - time.time()
                if left <= 0 or self.proc.poll() is not None:
                    raise AssertionError(
                        f"只收到 {len(self.lines)} 行（要 {count}）；stderr 尾巴："
                        f"{self.stderr_text[-400:]}")
                self._cond.wait(left)

    def parsed(self):
        return [json.loads(t) for t in list(self.lines)]

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _handshake(w):
    w.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "d7", "version": "0"}}})
    w.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    w.wait_for(1)


def _deep_arguments(depth=300):
    """一个真能上 wire 的超深参数（300 层，远超解析器能读的 ~92）。"""
    inner = None
    for _ in range(depth):
        inner = {"n": inner}
    return {"x": inner}


@pytest.fixture
def wire():
    w = _Wire()
    try:
        _handshake(w)
        yield w
    finally:
        w.close()


class TestABadLineGetsAnAnswerNotSilence:

    def test_an_over_deep_argument_is_answered(self, wire):
        """报告记的是"150 层正常返回"；实测 150 层已静默。修后该拿 -32700。"""
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state",
                              "arguments": _deep_arguments(300)}})
        wire.wait_for(2)
        err = wire.parsed()[1]
        assert err["id"] is None, f"解析错误该带 null id（JSON-RPC 2.0），拿到 {err}"
        assert err["error"]["code"] == -32700, err
        assert err["error"]["message"] == "Parse error", err

    def test_a_truncated_line_is_answered(self, wire):
        wire.send_raw('{"jsonrpc":"2.0","id":4,"method":"tools/li\n')
        wire.wait_for(2)
        err = wire.parsed()[1]
        assert err["id"] is None and err["error"]["code"] == -32700, err

    def test_a_line_that_is_not_json_at_all_is_answered(self, wire):
        wire.send_raw("not json at all\n")
        wire.wait_for(2)
        err = wire.parsed()[1]
        assert err["id"] is None and err["error"]["code"] == -32700, err

    def test_the_answer_names_the_cause(self, wire):
        """'Parse error' 三个字不告诉调用方是哪一颗输入错了；data 要说话。"""
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state",
                              "arguments": _deep_arguments(300)}})
        wire.wait_for(2)
        data = wire.parsed()[1]["error"]["data"]
        assert isinstance(data, str) and data, "data 必须说清是哪一类解析失败"
        assert "deeper" in data or "deep" in data, (
            f"嵌套过深这一类该被点名，拿到 {data!r}")


class TestTheSessionSurvivesAndOrderHolds:
    """坏行不许把连接搞坏，也不许把后面的正常应答挤掉或换序。"""

    def test_a_normal_call_after_three_bad_lines_still_answers(self, wire):
        wire.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state",
                              "arguments": _deep_arguments(300)}})
        wire.send_raw('{"jsonrpc":"2.0","id":4,"method":"tools/li\n')
        wire.send_raw("not json at all\n")
        wire.send({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})
        wire.wait_for(6)
        got = wire.parsed()
        assert [m.get("id") for m in got] == [1, 2, None, None, None, 5], (
            f"到达顺序不是握手 / 正常 / 三条解析错误 / 正常：{got}")
        assert [m["error"]["code"] for m in got if "error" in m] == [-32700] * 3
        # 坏行之后的正常应答必须真的是正常应答，不是被挤空的信封
        assert got[-1]["result"]["tools"], "坏行之后的 tools/list 该照常回工具表"

    def test_a_clean_session_gets_no_spurious_parse_error(self, wire):
        """反向守卫：全是好行时一条 -32700 都不许出现。"""
        wire.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        wire.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "sys_state", "arguments": {}}})
        wire.wait_for(3)
        got = wire.parsed()
        assert [m.get("id") for m in got] == [1, 2, 3]
        codes = [m["error"]["code"] for m in got if m.get("error")]
        assert -32700 not in codes, f"干净会话里不该出现解析错误：{got}"

    def test_the_wire_stays_quiet_on_stderr(self, wire):
        """修法不许靠打印来"说明"，也不许留下噪声。"""
        wire.send_raw("not json at all\n")
        wire.wait_for(2)
        time.sleep(0.4)
        assert wire.stderr_text == "", f"stderr 该是空的：{wire.stderr_text[:300]!r}"


# ─── 协议层：包装对象必须真的满足 ReadStream ────────────────────────

class _FakeRead:
    """一个最小读流：把条目原样交出去，条目就是异常时**返回**它（SDK 的写法）。"""

    def __init__(self, items):
        self._items = list(items)
        self.closed = False
        self.last_context = "sender-ctx"

    async def receive(self):
        if not self._items:
            raise anyio.EndOfStream
        return self._items.pop(0)

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)

    def __aiter__(self):
        return self

    async def aclose(self):
        self.closed = True


class _FakeWrite:
    def __init__(self):
        self.sent = []

    async def send(self, item, /):
        self.sent.append(item)


def _wrapper(inner):
    from dhole_mcp.server import _ParseErrorAnsweringReadStream
    write = _FakeWrite()
    return _ParseErrorAnsweringReadStream(inner, write), write


class TestTheWrapperIsFaithfulToTheReadStreamProtocol:

    def test_a_good_item_passes_through_untouched(self):
        sentinel = object()
        stream, write = _wrapper(_FakeRead([sentinel]))
        assert anyio.run(stream.receive) is sentinel
        assert write.sent == [], "好条目不许被改写，也不许顺手答一条错误"

    def test_every_bad_item_gets_its_own_answer(self):
        """连续坏行要各答一条 —— 循环，不是只处理第一条。"""
        stream, write = _wrapper(_FakeRead([ValueError("a"), ValueError("b"),
                                            ValueError("c")]))
        with pytest.raises(anyio.EndOfStream):
            anyio.run(stream.receive)
        assert len(write.sent) == 3, f"三条坏行该有三条回答，拿到 {len(write.sent)}"

    def test_the_anext_path_answers_too(self):
        stream, write = _wrapper(_FakeRead([ValueError("a"), "good"]))
        assert anyio.run(stream.__anext__) == "good"
        assert len(write.sent) == 1

    def test_the_stream_end_is_not_swallowed(self):
        """流结束必须原样抛出 —— 吞掉它就是让服务器永远不退出。"""
        stream, _ = _wrapper(_FakeRead([]))
        with pytest.raises(anyio.EndOfStream):
            anyio.run(stream.receive)
        stream2, _ = _wrapper(_FakeRead([]))
        with pytest.raises(StopAsyncIteration):
            anyio.run(stream2.__anext__)

    def test_aclose_reaches_the_inner_stream(self):
        inner = _FakeRead([])
        stream, _ = _wrapper(inner)
        anyio.run(stream.aclose)
        assert inner.closed, "aclose 必须转到底层，否则连接不会真的关"

    def test_the_sender_context_is_forwarded(self):
        """runner 靠 last_context 还原每条消息的 sender context；包一层不能丢。"""
        stream, _ = _wrapper(_FakeRead([]))
        assert stream.last_context == "sender-ctx"

    def test_it_can_be_used_as_an_async_context_manager(self):
        stream, _ = _wrapper(_FakeRead([]))

        async def _use():
            async with stream as s:
                return s is stream

        assert anyio.run(_use) is True

    def test_the_answer_is_a_proper_jsonrpc_error_frame(self):
        stream, write = _wrapper(_FakeRead([ValueError("Invalid JSON: boom")]))
        with pytest.raises(anyio.EndOfStream):
            anyio.run(stream.receive)
        frame = json.loads(write.sent[0].message.model_dump_json(
            by_alias=True, exclude_unset=True))
        assert frame == {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "Parse error",
                                   "data": "the line is not valid JSON"}}, frame
