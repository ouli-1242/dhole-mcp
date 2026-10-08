"""拒一个内网地址，要说的是地址，不是 URL 的拼法（2026-10-08 真实使用模拟 · 刀3）。

实测（真宿主 + 独立进程，同一轮约 70 次调用）：`http://127.0.0.1:8080/admin`、
`http://localhost:6379/`、`http://[::1]/`、`http://10.0.0.1/`、`http://0.0.0.0:8765/`
全部被拒 —— 拒得对。错的是随后那句建议：

  * web_read / web_request / web_act 给 "Correct the argument and call again -
    no request was made. **web_read takes an absolute http(s) URL** (e.g.
    https://example.com)."。可这条 URL 本来就是绝对 http(s)：那句话把读者推去改
    一个本来就对的东西。`quality.py` 的 `_invalid_request_result` 自己的 docstring
    写着"关于别的参数的拒绝必须自带建议"，这一支正是漏网的那一支。
  * web_crawl / web_discover 更糟："Fetch failed. Check the error field ... retry
    with different parameters." —— 一个包都没发，"retry" 是假出路。
  * feed_read 反倒是唯一说真话的：它点名 allow_private 那扇门。同一件事五种说法，
    本文件把它们收成一句。

契约（本文件钉住）：
  * 内网/私有地址的拒绝，next_action 不许出现"绝对 http(s) URL"这类拼法建议；
    它要说：地址被拒、什么都没发、真要取本机服务走 cred_defaults 的 allow_private。
  * 这句话只有一份（security.PRIVATE_ADDRESS_RECOVERY），六颗工具共用 —— 与
    RESOLVER_GUARD_RECOVERY 同一惯例。
  * 云元数据段按**规范化后的地址**认族：IPv4-mapped 拼法也要说出"metadata"，
    不许退化成一句泛泛的 private IP。
  * 真的拼法错（`htp:/x`、无主机名）照旧拿拼法建议 —— 这一刀不许把对的话改没。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import security
from dhole_mcp.security import PRIVATE_ADDRESS_RECOVERY, SecurityError
from dhole_mcp.server import MasterFetchServer

_PRIVATE = ["http://127.0.0.1:8080/admin", "http://10.0.0.1/", "http://[::1]/",
            "http://0.0.0.0:8765/x", "http://localhost:6379/"]


async def _envelope(tool: str, args: dict) -> dict:
    out = await MasterFetchServer()._dispatch(tool, args)
    rows = out[1] if isinstance(out, tuple) else out
    text = rows[0].text if not isinstance(rows, dict) else json.dumps(rows)
    return json.loads(text)


class TestAPrivateAddressSaysSo:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool,pack", [
        ("web_read", lambda u: {"url": u}),
        ("web_request", lambda u: {"url": u, "method": "HEAD"}),
        ("web_act", lambda u: {"url": u, "actions": [{"snapshot": True}]}),
    ])
    @pytest.mark.parametrize("url", _PRIVATE)
    async def test_the_advice_names_the_address_not_the_url_shape(self, tool, pack, url):
        d = await _envelope(tool, pack(url))
        assert "internal" in d["error"], d
        na = d["next_action"]
        assert "absolute http(s) URL" not in na, (
            f"又在教一条本来就写对的 URL 怎么写：{na[:120]}")
        assert "allow_private" in na, "本机服务那扇真正的门要在建议里"
        assert PRIVATE_ADDRESS_RECOVERY in na, "这句话全仓只许有一份"

    @pytest.mark.asyncio
    async def test_a_crawl_refusal_does_not_offer_a_retry(self):
        """一个包都没发的拒绝，不许说"换个参数再试"。"""
        d = await _envelope("web_crawl", {"url": "http://127.0.0.1:9/"})
        na = d.get("next_action", "")
        assert "retry with different parameters" not in na, na[:160]
        assert "allow_private" in na, na[:160]

    @pytest.mark.asyncio
    async def test_discover_and_feed_read_quote_the_same_sentence(self):
        for tool, args in (("web_discover", {"url": "http://127.0.0.1:9/"}),
                           ("feed_read", {"urls": ["http://127.0.0.1:9/f.xml"]}),
                           ("web_read", {"url": "http://127.0.0.1:9/"})):
            d = await _envelope(tool, args)
            blob = json.dumps(d, ensure_ascii=False)
            assert PRIVATE_ADDRESS_RECOVERY in blob, f"{tool} 还在自己编这句话"


class TestTheMetadataFamilyIsNamedByAddress:

    def test_an_ipv4_mapped_metadata_address_says_metadata(self):
        """泛泛的 "private IP" 抹掉了它是谁 —— 任何拼法都不放行这件事要靠话说清楚。"""
        with pytest.raises(SecurityError) as boom:
            security.validate_url("http://[::ffff:169.254.169.254]/latest/meta-data/")
        assert "metadata" in str(boom.value), str(boom.value)

    @pytest.mark.parametrize("spelling", [
        "169.254.169.254", "0xA9FEA9FE", "2852039166", "[::ffff:169.254.169.254]"])
    def test_no_spelling_of_the_metadata_address_gets_an_allowlist_exit(self, spelling):
        from dhole_mcp import identity
        identity.set_value("allow_private", [spelling.strip("[]")])
        try:
            host = f"[{spelling}]" if spelling.startswith("[") else spelling
            with pytest.raises(SecurityError):
                security.validate_url(f"http://{host}/latest/meta-data/")
        finally:
            identity.set_value("allow_private", None)


class TestATypedUrlStillGetsUrlAdvice:

    @pytest.mark.asyncio
    async def test_a_malformed_url_keeps_the_shape_lesson(self):
        d = await _envelope("web_read", {"url": "not-a-url-at-all"})
        assert "http(s)" in d["next_action"], (
            "真拼错的 URL 不该被换成一段内网教程")
