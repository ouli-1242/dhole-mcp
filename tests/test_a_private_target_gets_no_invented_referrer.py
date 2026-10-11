"""验收刀9-1（F7）：打自己机器上的服务时，dhole 不该把那套伪装头一起发进去。

缺陷的原文是一句人的话："我啥时候发的 google referer？"（2026-10-11 全真模拟 S9，
`web_request` POST 到 `127.0.0.1:8901/echo`）。量到的事实是：一次普通的 `web_read`
往站点发 15 颗头 —— 一枚编造的 `referer: https://www.google.com/`、browserforge 生成
的 UA 与 `sec-ch-ua*` —— 一颗都不是调用方点的。那些头在公网上是**过墙的承重件**
（`test_a_header_you_named_is_a_header_the_site_receives.py` 钉着它不许被顺手删掉），
但打回环/内网时墙不存在，它们只剩一个去处：用户自己服务的日志。

而调用方手里没有关的把手：`anti_detect=` 是 `web_read`/`web_act` 的参数，`web_request`
故意不收它（那条文案在 `server.py:3089-3097` 说清了理由）。所以这一刀不是加一颗旋钮，
是让 dhole 在**判定得出"这是你自己的机器"**的时候自己脱掉：判据用既有的
`security.target_is_private`（地理，不是许可 —— 门还开着才轮得到发请求这一步）。

四条边界，每条都有人付账：

* 公网逐位不变。`auto` 档的形状由 `test_auto_is_bit_for_bit_the_pre_refactor_default`
  钉着；这里再加一条对照 —— 同一次调用把 URL 换成公网，`stealthy_headers` 回到 True
  且 summary 里一个字都不许多。
* 你点名的每一颗照旧到线上。摘的是 dhole 自己合成那一份，不是"这次只发裸请求"。
* 话由扳下它的那一层说。档位 `off` 与内网自动脱是**两个扳法两句原因**：把
  `anti_detect='off'` 写在回环那一发上，等于让用户去找一把他没碰过、这扇门上也不存在
  的开关；反过来把内网那句写在 `off` 那一发上，就是给一次没发生的自动判断做证。
* 摘要里不许出现"Chrome profile"之外的第二份 UA 说法 —— 合成模板被摘掉时 primp 自己
  那一份仍在，这一点与 `off` 档说的是同一件事，所以两句话共用同一个尾巴。
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socket
import threading

import pytest

from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

PUBLIC = "https://example.com/"
PAGE = (b"<html><head><title>T</title></head><body><p>"
        + b"Real body text so the extractor has something to chew on. " * 4
        + b"</p></body></html>")
GOOGLE = "https://www.google.com/"

# 靶站是真 socket，而 conftest 的 `_offline_dns`（autouse）把 getaddrinfo 钉成公网地址。
# 真解析器必须在**夹具里**换回来（先例见本目录同名形状的那一条）。
_REAL_GETADDRINFO = socket.getaddrinfo


class _Recorder:
    def __init__(self):
        self.received: list[tuple[str, dict]] = []

    def handler(self):
        rec = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                rec.received.append((self.path,
                                     {k.lower(): v for k, v in self.headers.items()}))
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)

            do_GET = do_POST = _answer

        return Handler

    def page_headers(self) -> dict:
        hits = [h for p, h in self.received if p.split("?")[0] == "/"]
        assert hits, f"页面从未被请求：{[p for p, _ in self.received]}"
        return hits[-1]


@pytest.fixture
def echo(monkeypatch):
    """一台真回环靶站，门开着 —— F7 复现时的形状就是"用户自己的服务在听"。"""
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    rec = _Recorder()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), rec.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    identity.set_value("allow_private", True)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/", rec
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(autouse=True)
def private_door_open():
    """这一族测的是"到了那台机器门口还发什么头"，不是"能不能进门"。

    门（`cred_defaults` 的 `allow_private`）关着时私网目标一发都不发，那一条由
    `test_a_private_target_is_refused_until_you_open_the_door` 一族的守卫管；把门开在
    这里，红了才读得出是**头**变了而不是门变了。
    """
    identity.set_value("allow_private", True)


def _call(tool: str, args: dict):
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _stub_get(monkeypatch) -> list[dict]:
    """记下交给 HTTP 层的每一发参数；真包不许在这条路上发出去。"""
    seen: list[dict] = []

    async def fake(self, url, **kwargs):
        seen.append(dict(kwargs, url=url))
        return server_mod.ResponseModel(url=url, status=200, content=["body text"],
                                       fetcher_used="http", content_ok=True,
                                       content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    return seen


# ─────────────── 真线上：回环目标收不到编造的来路 ───────────────

def test_a_loopback_read_arrives_without_dholes_invented_referrer(echo):
    url, rec = echo
    out = _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                             "ignore_robots": True})
    got = rec.page_headers()
    assert "referer" not in got, f"回环目标收到了编造的来路: {got}"
    summary = out.get("summary") or ""
    assert "loopback/private" in summary, summary
    assert "no fabricated google referer" in summary, summary


def test_a_loopback_post_is_no_different_from_a_get(echo):
    """S9 的原始形状是 POST。摘头这件事不该只管读的那一半 —— 它管的是这一发要去谁家。

    `web_request` 收不下 `cache_ttl` / `ignore_robots`（它那一族只有八个名字），所以这条
    就是当年那一次调用：方法、URL、点名的头、body。
    """
    url, rec = echo
    out = _call("web_request", {"method": "POST", "url": url, "confirm": True,
                                "body": {"k": "v"}})
    assert out.get("status") == 200, out
    got = rec.page_headers()
    assert "referer" not in got, f"POST 到回环还带着编造的来路: {got}"
    assert "loopback/private" in (out.get("summary") or ""), out.get("summary")


def test_the_headers_you_named_still_arrive_when_dhole_drops_its_own(echo):
    """摘的是 dhole 合成的那一份，不是把这一发降级成裸请求。"""
    url, rec = echo
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True,
                       "headers": {"x-debug": "1", "referer": "http://self.test/"}})
    got = rec.page_headers()
    assert got["x-debug"] == "1", got
    assert got["referer"] == "http://self.test/", got


# ─────────────── 公网：一条字节都不许多发，也一条字都不许多说 ───────────────

@pytest.mark.parametrize("target", [PUBLIC, "https://sub.example.com/page"])
def test_a_public_target_keeps_the_template(monkeypatch, target):
    seen = _stub_get(monkeypatch)
    out = _call("web_read", {"url": target, "force_fetcher": "http", "cache_ttl": 0})
    assert seen[0]["stealthy_headers"] is True, seen[0]
    assert "loopback/private" not in json.dumps(out), out


def test_a_loopback_by_name_drops_them_like_a_loopback_by_number(monkeypatch):
    """摘不摘看的是**这一发落到哪台机器**，不是它写成了 IP 还是主机名。"""
    seen = _stub_get(monkeypatch)
    for target in ("http://127.0.0.1:8901/echo", "http://localhost:8901/echo"):
        _call("web_read", {"url": target, "force_fetcher": "http", "cache_ttl": 0,
                           "ignore_robots": True})
    assert [s.get("stealthy_headers") for s in seen] == [False, False], seen


def test_the_private_judgement_is_geography_not_a_port_guess():
    """判据只有 `security.target_is_private` 一个作者：网段说了算，端口不算。

    这一条为什么不通过一次真调用去量：门（`cred_defaults` 的 `allow_private=true`）只放
    回环，RFC1918 要一份主机名册 —— 拿 10.0.0.7 测会先撞上那道门，红起来读不出是头
    变了还是门变了。所以这里量判定本身，公网 IP 字面量也必须留在假的那一侧。
    """
    for target in ("http://192.168.1.20/api", "http://10.0.0.7/x", "http://[fd00::1]/x",
                   "http://localhost:8901/echo", "http://127.0.0.1:8901/echo"):
        assert MasterFetchServer._target_is_private(target) is True, target
    for target in (PUBLIC, "https://8.8.8.8/", "http://example.com:8901/echo"):
        assert MasterFetchServer._target_is_private(target) is False, target


def test_one_batch_row_drops_and_the_other_does_not(monkeypatch):
    """`urls=[...]` 是第二条路：每一行自己判定，不共享第一行的决定。

    ContextVar 按任务复制，所以批量里混一个公网一个回环时，摘头这件事必须逐行发生 ——
    否则"你的头只发到你自己的机器上"会变成"这一批都不伪装"。
    """
    seen = _stub_get(monkeypatch)
    _call("web_read", {"urls": ["http://127.0.0.1:8901/echo", PUBLIC],
                       "force_fetcher": "http", "cache_ttl": 0})
    by_url = {s["url"]: s for s in seen}
    assert by_url["http://127.0.0.1:8901/echo"]["stealthy_headers"] is False, by_url
    assert by_url[PUBLIC]["stealthy_headers"] is True, by_url


# ─────────────── 两个扳法，两句原因 ───────────────

def test_the_rung_still_names_itself_on_a_loopback_target(monkeypatch):
    """`anti_detect='off'` 打回环时说的是档位，不是"自动"—— 那一发没有任何自动判断。"""
    seen = _stub_get(monkeypatch)
    out = _call("web_read", {"url": "http://127.0.0.1:8901/echo",
                             "force_fetcher": "http", "cache_ttl": 0,
                             "anti_detect": "off"})
    assert seen[0]["stealthy_headers"] is False, seen[0]
    summary = out.get("summary") or ""
    assert "anti_detect='off'" in summary, summary
    assert "loopback/private" not in summary, summary


def test_the_auto_drop_is_said_once(monkeypatch):
    """一次改变一句账：`headers=` 的那句机制说明不许再跟着点一遍自动脱伪装。"""
    _stub_get(monkeypatch)
    out = _call("web_read", {"url": "http://127.0.0.1:8901/echo",
                             "force_fetcher": "http", "cache_ttl": 0,
                             "headers": {"x-a": "1"}})
    blob = json.dumps(out, ensure_ascii=False)
    assert blob.count("no fabricated google referer") == 1, blob
    assert "loopback/private" in blob, blob
