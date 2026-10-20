# -*- coding: utf-8 -*-
"""存量 `allow_private` 是一把门，不是每扇门各自的一件私事（评审 R02-141 · F122）。

量到的形状（真机 `review/.runtime/p1/r329_guard_side_cost.json`，同一进程、同一存储、
不重启不设 env）：`identity.set_value("allow_private", True)` 之后
`web_search(mode=find_similar, url=http://127.0.0.1:<port>/captcha)` 仍交回
`invalid_request: URL targets internal/private IP (127.0.0.1 in 127.0.0.0/8)`。
同一颗存在 `web_read` / `web_crawl` / `feed_read` / `web_request` 上放行，在搜索扇上
不存在 —— 手册把它写成一把通用的门（`configuration.md`：`cred_defaults` 是
`DHOLE_ALLOW_PRIVATE_HOSTS` 的"tool-side equivalent"），而等价物只在记得推作用域的门上成立。

来路只有一条：作用域由各扇门自己从存储里读一次再 push（crawl.py 与 server.py 共四处），
所以第五扇要修就得写第五处。本仓为这件事已经付过一次账 —— crawl 那颗种子当年就是
`feed_read` 那一刀的漏扇。**修法选守卫侧**：`private_hosts_allowed()` 像并上 env 那样并上
存量，凡走守卫的路都算；四处 push 因此删掉，存量值只剩一位读者。代价量过（r329）：
`identity.get` 4.4µs × 每发 `validate_url` 读 3 次 = 每发 13µs，500 页爬取共 6.6ms。

下面每一支都钉这个形状；三处反抄（carve-out）一寸不松：云元数据永远不在这里返回 True，
点名名单仍是字面匹配，`allow_private_scope` 那层机制照样能用。
"""

from __future__ import annotations

import ast
import inspect
import socket
import threading
from pathlib import Path

import pytest

import dhole_mcp.search as search_mod
from dhole_mcp import identity, security
from dhole_mcp.server import MasterFetchServer

EVALS = Path(__file__).resolve().parent.parent / "evals"
SRC = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp"

PRIVATE_TOKENS = ("private IP", "internal/private", "internal service",
                  "127.0.0.1 in 127.0.0.0/8")

_REAL_GETADDRINFO = socket.getaddrinfo


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    """真起一台本机靶站（0 号端口），每条用例换一个端口 = 缓存键也换一个。"""
    import sys

    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    sys.path.insert(0, str(EVALS))
    import fixture_site as fs

    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    srv = fs.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def no_engine_traffic(monkeypatch):
    """搜索扇不许因为这把刀就开始真的问引擎：任何一次 `multi_search` 都是失败。"""
    asked = []

    async def _spy(*a, **k):
        asked.append(a)
        return [], []

    monkeypatch.setattr(search_mod, "multi_search", _spy)
    return asked


def _say(out) -> str:
    """把这一发说过的话收成一段文本 —— 各扇的信封形状不同，判据只问说了什么。"""
    if isinstance(out, (list, tuple)):
        parts = []
        for item in out:
            if isinstance(item, dict):
                parts.extend(str(v) for v in item.values() if isinstance(v, str))
            else:
                parts.append(str(item))
        return " | ".join(parts)
    if isinstance(out, dict):
        return " | ".join(str(v) for v in out.values() if isinstance(v, str))
    return " | ".join(str(getattr(out, k, "") or "") for k in
                      ("error", "summary", "next_action", "message"))


def _face(name: str, base: str):
    """Drive one door at one loopback target and say whatever came back."""
    srv = MasterFetchServer()
    calls = {
        "web_read": lambda: srv.web_read(url=f"{base}/about"),
        "web_crawl": lambda: srv.web_crawl(url=f"{base}/collections?items=3",
                                           max_pages=2, max_depth=1),
        "web_discover": lambda: srv.web_discover(url=base),
        "feed_read": lambda: srv.feed_read(urls=[f"{base}/feed.xml"]),
        "web_search": lambda: srv.web_search(query="widgets", mode="find_similar",
                                             url=f"{base}/captcha"),
    }
    try:
        import asyncio

        return _say(asyncio.run(calls[name]()))
    except Exception as exc:  # a face that raises still owes the same sentence
        return f"{type(exc).__name__}: {exc}"


FACES = pytest.mark.parametrize("face", sorted(
    ("web_read", "web_crawl", "web_discover", "feed_read", "web_search")))


class TestOneJudgeOnEveryFace:
    """同一颗存量，在五扇上必须是同一个裁判。"""

    @FACES
    def test_without_the_stored_door_every_face_refuses_loopback(self, face, live_site):
        said = _face(face, live_site)
        assert any(t in said for t in PRIVATE_TOKENS), \
            f"{face} 在没有存量的时候竟然不拒回环：这一支是空的判据 {said!r}"

    @FACES
    def test_the_stored_door_opens_on_every_face(self, face, live_site,
                                                 no_engine_traffic):
        identity.set_value("allow_private", True)
        said = _face(face, live_site)
        assert not any(t in said for t in PRIVATE_TOKENS), \
            f"存了 allow_private 之后 {face} 仍按私网拒绝: {said!r}"
        assert no_engine_traffic == [], \
            f"{face} 为了放开回环开始问引擎了: {no_engine_traffic}"


class TestTheFaceThatNeverHadTheDoor:
    """搜索扇那条腿要走到它自己的那一面，不是含糊地"没拒"。"""

    def test_the_source_leg_reaches_the_page_it_came_to_read(self, live_site,
                                                             no_engine_traffic):
        identity.set_value("allow_private", True)
        said = _face("web_search", live_site)
        # 靶站 /captcha 那一面的自有判词（F121 立的那批 token）—— 到了这里才算真进门
        assert "bot_wall_detected" in said, said
        assert no_engine_traffic == []

    def test_the_same_url_without_the_door_stops_at_the_address(self, live_site,
                                                                no_engine_traffic):
        said = _face("web_search", live_site)
        assert "127.0.0.1 in 127.0.0.0/8" in said, said
        assert "bot_wall_detected" not in said


class TestTheCarveOutsStayShut:
    """收敛的是作用域，不是闸门的宽度（§12：安全闸不许因修而松）。"""

    @pytest.mark.parametrize("metadata", [
        "169.254.169.254", "100.100.100.200", "metadata.google.internal",
    ])
    def test_a_stored_door_naming_metadata_still_refuses_it(self, metadata):
        identity.set_value("allow_private", [metadata])
        with pytest.raises(security.SecurityError) as exc:
            security.validate_url(f"http://{metadata}/latest/meta-data/")
        assert "metadata" in str(exc.value).lower(), exc.value

    def test_the_loopback_branch_does_not_widen_to_the_whole_lan(self):
        identity.set_value("allow_private", True)
        with pytest.raises(security.SecurityError):
            security.validate_url("http://192.168.1.1/admin")

    def test_a_named_host_is_still_matched_literally(self):
        """点名 `127.0.0.1` 不等于放行八进制写法：那条路本来就是给绕过准备的。"""
        identity.set_value("allow_private", ["127.0.0.1"])
        hosts, loopback = security.private_hosts_allowed()
        assert "127.0.0.1" in hosts and loopback is False
        assert security._private_host_allowed("0177.0.0.1") is False

    def test_no_stored_value_leaves_the_default_of_reject_everything(self):
        assert security.private_hosts_allowed() == (frozenset(), False)
        with pytest.raises(security.SecurityError):
            security.validate_url("http://127.0.0.1/")


class TestOneReaderOfTheStoredValue:
    """存量值只许有一位读者：守卫。谁再抄一次 push，这一支就红。"""

    def test_the_guard_reads_the_store_with_no_scope_pushed(self):
        """没有门推作用域时，`private_hosts_allowed()` 自己就把存量并进来。"""
        identity.set_value("allow_private", ["my-box.local"])
        hosts, _ = security.private_hosts_allowed()
        assert "my-box.local" in hosts, hosts

    def test_env_and_store_and_a_scope_all_merge_into_one_answer(self):
        identity.set_value("allow_private", ["from-the-store.local"])
        with security.allow_private_scope(["from-the-scope.local"]):
            hosts, _ = security.private_hosts_allowed()
        assert {"from-the-store.local", "from-the-scope.local"} <= hosts, hosts

    def test_the_store_is_not_read_into_a_scope_anywhere_in_src(self):
        """`set_allow_private` / `allow_private_scope` 在 src 里只许住在定义它的那一页。

        判据按 AST 的调用名数，不按文本 —— 注释与文档里提它不算用它。修之前这一支红在
        四处（crawl.py 一处、server.py 三处）：那四处是同一件事的四个名字，第五扇
        （`web_search`）就是它们中间漏掉的那一个。
        """
        pushers = []
        for path in sorted(SRC.glob("*.py")):
            if path.name == "security.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in ("set_allow_private",
                                             "allow_private_scope")):
                    pushers.append(f"{path.name}:{node.lineno}")
        assert pushers == [], f"作用域又被抄了一次: {pushers}"

    def test_the_store_has_exactly_one_reader_of_allow_private(self):
        """读存量这件事在 src 里只许出现在守卫那一步。"""
        readers = []
        for path in sorted(SRC.glob("*.py")):
            src = path.read_text(encoding="utf-8")
            for needle in ('get("allow_private")', "get('allow_private')"):
                if needle in src:
                    readers.append(path.name)
                    break
        assert readers == ["security.py"], readers


class TestTheStoredValueThatDoesNothingIsStillSaid:
    """删掉门侧那一次读，不许把「你存的这颗没用」那句话一起删掉。"""

    def test_a_value_that_parses_to_nothing_opens_nothing(self, live_site):
        """`[]` 并进门里也仍然是一个都不放 —— 合并不是"存了什么都算开门"。"""
        identity.set_value("allow_private", [])
        hosts, loopback = security.private_hosts_allowed()
        assert hosts == frozenset() and loopback is False
        assert any(t in _face("web_read", live_site) for t in PRIVATE_TOKENS)

    def test_the_note_is_still_said_when_the_call_gets_through(self, live_site):
        """存量值成不成立与这一次发不发得出去是两句话，前者不许被后者吃掉。

        这一发走的是作用域那层机制开的门（存量 `[]` 什么也没开），所以正文取回来了，
        而「你存的那颗没用」仍然要在 summary 里说 —— 这一支钉的是"门的读者换了人，
        报账的那一句还活着"。
        """
        import asyncio

        identity.set_value("allow_private", [])
        with security.allow_private_scope(True):
            out = asyncio.run(MasterFetchServer().web_read(url=f"{live_site}/about"))
        assert not out.error, out.error
        said = _say(out)
        assert "does nothing" in said, said
        assert "true (loopback only)" in said, said


class TestTheMechanismItselfIsStillUsable:
    """`allow_private_scope` 没有被这次收敛废掉：它仍是就地开一层的机制。"""

    def test_a_scope_without_any_stored_value_still_opens_its_host(self):
        with pytest.raises(security.SecurityError):
            security.validate_url("http://10.0.0.5/api")
        with security.allow_private_scope(["10.0.0.5"]):
            assert security.validate_url("http://10.0.0.5/api")
        with pytest.raises(security.SecurityError):
            security.validate_url("http://10.0.0.5/api")


class TestTheLawIsStatedWhereTheDoorSpeaks:
    """手册与出路句子不许再把它说成"抓取那几扇才有的一颗参数"。"""

    def test_no_manual_page_calls_it_a_fetch_only_option(self):
        skills = (SRC / "skills" / "dhole-web" / "references")
        offenders = []
        for path in sorted(skills.glob("*.md")):
            text = path.read_text(encoding="utf-8").lower()
            if "allow_private" in text and any(
                    phrase in text for phrase in
                    ("only web_read", "only for web_read", "fetch tools only")):
                offenders.append(path.name)
        assert offenders == [], offenders

    def test_the_guard_side_helper_is_the_one_that_documents_this(self):
        doc = inspect.getdoc(security.private_hosts_allowed) or ""
        assert doc, "守卫这一步的合并必须有自己的一句话"
        assert "存量" in doc or "cred_defaults" in doc, doc
