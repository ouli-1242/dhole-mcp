"""存量身份必须参与缓存指纹 —— §3-17 搬家时断掉的那根管子。

`_cache_context` 从 G7/G25 时代就消费 cookies / auth / proxy（"它们改变回来的是
什么"），但 17.0 把 auth / cookies 从调用参数搬进 `cred_defaults` 之后，指纹的
输入源还留在旧地址上：装饰器只看 `options`，而 options 里早就没有这三个键了。
后果是「先匿名抓、再存身份、再抓同 URL」命中设身份之前的缓存——回执的
`identity_applied` 声称这次身份在生效，内容却是匿名那一份（2026-10-05 那次真实使用
全真模拟抓到，wire 组有真协议存证）。

修法在 `_smart_fetch_request_context`：构建指纹前把**存量** auth / cookies
（按这次请求的 host 取，与 `_identity_for` 同一判定）与 proxy（全局键）并回
输入。这里的判据是行为而不是指纹字符串本身——数 `_force_fetch` 被真调了几次：

  - 匿名读两次 → 只取一次（缓存命中，与修前一致）；
  - 存下 auth → 同 URL 再读必须**重取**（指纹变了），回执点名 auth；
  - unset → 再读回到缓存（匿名那份本来就还在，身份状态的每个取值各占一个槽）；
  - 为别的 host 存的凭据不进这台的指纹（作用域语义与 `_identity_for` 一致）。
"""

from __future__ import annotations

import asyncio


from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer, ResponseModel

URL = "https://staging.example.com/admin"
HOST = "staging.example.com"


def _counting_fetch(monkeypatch) -> list:
    """`_force_fetch` 的桩：记下每次真取，回的正文带序号以便区分新旧。

    桩打在这里而不是更深的层上，与 `test_cred_defaults_b5` 同一裁决：这是档位参数
    到齐的最后一站。配套地，下面的读全部钉 `force_fetcher="http"`——普通梯子走
    `_auto_escalate`，不经过这颗方法；而缓存查找坐在层选择**之前**，钉层不改变
    被测的行为（谁进缓存、谁被命中），只把路径收窄成确定的一根。
    """
    calls: list = []

    async def fake(self, url, force_fetcher=None, extraction_type="markdown",
                   css_selector=None, cache_ttl=3600, offset=0,
                   max_chars=40000, **kwargs):
        calls.append(url)
        result = ResponseModel(url=url, status=200,
                               content=[f"body-{len(calls)}"],
                               fetcher_used=force_fetcher or "http")
        # 真 _force_fetch 的收尾就是 _finalize_result（标注质量 → 写缓存 → 切块）；
        # 桩不接上它，写缓存就跟着桩一起消失了，第二次读永远 MISS。
        return await self._finalize_result(result, url, extraction_type,
                                           css_selector, cache_ttl, offset,
                                           max_chars)

    monkeypatch.setattr(MasterFetchServer, "_force_fetch", fake)
    return calls


def _read(url: str = URL) -> ResponseModel:
    return asyncio.run(MasterFetchServer().web_read(url, force_fetcher="http"))


def test_anonymous_reads_still_share_the_cache_entry(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _read()
    second = _read()
    assert len(calls) == 1, "第二次读应命中缓存"
    assert "body-1" in second.content


def test_stored_auth_forces_a_refetch_and_names_itself(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("auth", {"type": "basic", "username": "u", "password": "p"},
                       scope=HOST)
    out = _read()
    assert len(calls) == 2, "存了身份后同 URL 必须重取，不许端出匿名缓存"
    assert "body-2" in out.content
    assert "auth" in (out.identity_applied or ""), out.identity_applied


def test_unset_returns_to_the_anonymous_entry(monkeypatch):
    """身份的每个取值各占一个缓存槽：unset 之后匿名那份仍然有效。"""
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("auth", {"type": "basic", "username": "u", "password": "p"},
                       scope=HOST)
    _read()
    identity.unset(["auth"])
    out = _read()
    assert len(calls) == 2, "unset 后应命中匿名的旧槽，而不是第三次真取"
    assert "body-1" in out.content


def test_a_credential_for_another_host_does_not_open_this_cache_slot(monkeypatch):
    """host 作用域是身份的门，也是缓存槽的门 —— 两者必须同一判定。"""
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("auth", {"type": "basic", "username": "u", "password": "p"},
                       scope="other.example.org")
    out = _read()
    assert len(calls) == 1, "别台的凭据不该让这台重取"
    assert "body-1" in out.content


def test_stored_proxy_counts_as_identity_too(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("proxy", "http://127.0.0.1:7890")
    out = _read()
    assert len(calls) == 2, "存量代理改变出口，出口可能改变内容——必须重取"
    assert "body-2" in out.content
    identity.unset(["proxy"])
    out = _read()
    assert len(calls) == 2, "撤掉代理后回到直连的旧槽"
    assert "body-1" in out.content
