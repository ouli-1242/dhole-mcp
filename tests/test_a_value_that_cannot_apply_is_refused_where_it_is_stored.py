# -*- coding: utf-8 -*-
"""存进去就用不上的值，要在存的那一侧就响（评审 R02-104 · R02-105）。

量到的形状（两趟真机 · HEAD 0675f15 · pid 19372 · 全部本机或门口返回）：

  `r132_store_value_shapes.json`（32 发）
    · `cdp` 的 `9222` / `localhost:9222` 与 `profile` 的空白串 / 像路径的值 —— **存的那一刻就具名拒绝**；
    · `proxy` 的四种写法（`127.0.0.1:7890` / 完整 URL / 只有主机 / `not a url at all`）**全部收下**，
      `show` 报 `1 default(s) in force: proxy.`
  `r132b_broken_proxy.json`（6 发）
    · 带着那颗坏 proxy 打一发：`invalid_request: Invalid proxy URL: Proxy scheme must be http,
      https, socks5, or socks5h, got: ` · `status=0` · `fetcher=none`（一句都没出去，拒得对）——
      错的是**谁来说**：出路写 `web_read takes an absolute http(s) URL`，教的是这颗 URL 的拼法，
      要改的却是 `cred_defaults` 里那颗存值（R02-096 那一族的第二颗：被拒的不是这一发的参数）。
  `r127_allow_private_shapes.json`（18 发）
    · `allow_private` 三颗写法开门（`true` / `["127.0.0.1"]` / `["localhost"]`），
      带端口那颗 `["127.0.0.1:8399"]` **收下、报告在 force、却永远匹配不上**
      （`parse_allow_private` 按 URL 里的字面主机名建集合，端口不是主机名的一部分）。

契约（本文件钉住）：
  * **一颗值能不能用，只许有一位裁判**：`proxy` 存的那一侧必须与 `security.validate_proxy`
    同判（逐形状对照，不靠措辞）；`allow_private` 的每一颗条目必须是主机名形状（带端口/方案/路径
    的当场拒绝，并说清"端口在这颗值里没有匹配对象"）。
  * 存储门的每一种拒绝都带 `invalid_request:`、行首是本门那句 `Nothing was stored or forgotten.`，
    且出路点名要改的那扇门（`cred_defaults`），不许由读门代答成 `url=` 的拼法课。
  * 收紧不许变放宽：`unset` 才是关门的写法、云元数据仍不给出口，这两条照旧钉。
"""
from __future__ import annotations

import json

import pytest

from dhole_mcp import security
from dhole_mcp.server import MasterFetchServer, _identity_value_error

CLAUSE = "Nothing was stored or forgotten."

PROXY_SHAPES = [
    "not a url at all",                 # r132 量到：收下，用到时才炸
    "127.0.0.1",                        # 没有 scheme
    "127.0.0.1:7890",                   # 只有 host:port
    "http://127.0.0.1:7890",            # 正常
    "socks5://127.0.0.1:1080",          # 正常
    "ftp://127.0.0.1:21",               # scheme 不在白名单
    {"server": "127.0.0.1", "port": 7890},   # dict 形状（按 validate_proxy 的规矩）
]
AP_HOSTS = [
    pytest.param(["127.0.0.1"], True, id="只有主机（有效写法）"),
    pytest.param(["localhost"], True, id="主机名（有效写法）"),
    pytest.param(["127.0.0.1:8399"], False, id="带端口（r127 量到收下却不匹配）"),
    pytest.param(["http://127.0.0.1"], False, id="带方案"),
    pytest.param(["127.0.0.1/secret"], False, id="带路径"),
    pytest.param(True, True, id="布尔 true（回环那一档）"),
]


async def _face(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("cred_defaults", args)
    d = json.loads("".join(getattr(b, "text", "") for b in res.content))
    return d


class TestOneRefereePerValue:
    """`proxy`：存的那一侧与用的那一侧必须同判。"""

    @pytest.mark.parametrize("shape", PROXY_SHAPES, ids=[str(s)[:26] for s in PROXY_SHAPES])
    def test_the_store_agrees_with_the_fetcher(self, shape):
        try:
            security.validate_proxy(shape)
            usable = True
        except security.SecurityError:
            usable = False
        refused_at_store = bool(_identity_value_error("proxy", shape))
        assert usable is not refused_at_store, (
            f"用得了却存不下 / 用不了却存得下：shape={shape!r} "
            f"usable={usable} refused={refused_at_store}")

    def test_the_value_that_burned_next_call_is_now_refused_here(self):
        """R02-105 那一颗：`not a url at all` 曾被收下并报 in force。"""
        assert _identity_value_error("proxy", "not a url at all"), "这颗还是存得下"

    @pytest.mark.asyncio
    async def test_the_refusal_points_at_the_store_door_not_the_url_spelling(self):
        d = await _face({"set": {"proxy": "not a url at all"}})
        na = d.get("next_action") or ""
        assert d["error"].startswith("invalid_request:"), d["error"]
        assert na.startswith(CLAUSE), na[:200]
        assert "cred_defaults" in na or "proxy=" in na, f"出路没点名要改的那颗: {na[:200]}"
        assert "takes an absolute http(s) URL" not in na, na[:200]

    @pytest.mark.asyncio
    async def test_the_fetch_door_names_the_proxy_it_refused(self):
        """R02-105 量到的那一发（`r132b`）：`error` 点名 proxy，出路教的却是 URL 的拼法。

        这一支今天仍然有人走：走它的是**老版本写进库里的值**（存储那一侧从这一版起才问
        `validate_proxy`），所以照 `identity.set_value` 那条底层路把它放进去 —— 门那一侧
        已经存不下，这本身就是这一把的另一半判据（上面那格钉着）。
        17.0 的抓取工具不收 `proxy=`（`_TOP_LEVEL_ARGS` 里 0 颗），所以出路必须点名
        `cred_defaults`，也不许再教 `url=` 的拼法。URL 用公网写法那一颗，免得私网门先响。
        """
        from dhole_mcp import identity

        identity.set_value("proxy", "not a url at all")
        try:
            r = await MasterFetchServer()._dispatch(
                "web_read", {"url": "https://example.com"})
        finally:
            identity.unset(["proxy"])
        rows = r[1] if isinstance(r, tuple) else r
        d = rows if isinstance(rows, dict) else json.loads(rows[0].text)
        err = d.get("error") or ""
        na = d.get("next_action") or ""
        assert "roxy" in err, err[:200]
        assert "takes an absolute http(s) URL" not in na, na[:240]
        assert "cred_defaults" in na, na[:280]
        # 出路不许把读者送去改一颗不存在的参数。这一格的写法被两条旧闸共同裁定过：
        # 句子要说「本发不带 proxy 这颗参数」，但**不许**写成 `proxy=` 那种调用形状
        # （`test_tool_descriptions` 的退役参数闸钉着：写了就等于让调用方以为自己试过了）。
        assert "no proxy argument" in na, na[:280]
        # 「没有东西出去过」得是**这一句**：读门的动词，不是存储门的话。
        assert na.startswith("Nothing was fetched"), na[:200]


class TestAHostIsNotAHostPort:
    """`allow_private`：条目必须是主机名形状 —— 端口在这颗值里没有匹配对象。"""

    @pytest.mark.parametrize("value,usable", AP_HOSTS)
    def test_only_spellings_that_can_match_are_stored(self, value, usable):
        refused = bool(_identity_value_error("allow_private", value))
        assert usable is not refused, f"{value!r} 该{'存得下' if usable else '当场被拒'}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad,needle", [
        (["127.0.0.1:8399"], "port"),
        (["http://127.0.0.1"], "scheme"),
        (["127.0.0.1/x"], "path"),
        (["bad host name!"], "space"),
    ])
    async def test_the_refusal_says_why_a_port_does_not_belong_here(self, bad, needle):
        """「为什么这一颗匹配不上」要说得出**这一种写法**的名字。

        判据的位置按信封契约改过一次（评审 R02-087/098 立的那条轴）：成因住 `error`，
        门话与出路住 `next_action` —— 所以这一格读的是这一发的**整份回执**，而不是硬把
        成因塞进出路那一句。
        """
        d = await _face({"set": {"allow_private": bad}})
        whole = (d.get("error") or "") + " " + (d.get("next_action") or "")
        assert d["error"].startswith("invalid_request: allow_private:"), d["error"][:160]
        assert (d.get("next_action") or "").startswith(CLAUSE), whole[:220]
        assert needle in whole.lower(), f"没点名这一种写法: {whole[:260]}"
        assert "cred_defaults" in whole, whole[:220]


class TestTheGatesDidNotMove:
    """收紧不许顺手放宽：关门仍是 unset、云元数据仍不给出口。"""

    @pytest.mark.asyncio
    async def test_closing_the_door_is_still_unset(self):
        msg = _identity_value_error("allow_private", False)
        assert "unset" in msg, msg

    @pytest.mark.asyncio
    async def test_metadata_endpoints_still_have_no_waiver(self):
        d = await _face({"set": {"allow_private": ["169.254.169.254"]}})
        # 量到的事实：这一颗目前存得下（列表按主机名建集合）；云元数据在守卫那侧**永远拒绝**。
        # 这一格钉的是"别把存储校验当成放行"：即便存下，读那一发仍然必须被拒。
        if not d.get("error"):
            await MasterFetchServer()._dispatch("cred_defaults", {"unset": ["allow_private"]})
            r = await MasterFetchServer()._dispatch(
                "web_read", {"url": "http://169.254.169.254/latest/meta-data/"})
            rows = r[1] if isinstance(r, tuple) else r
            payload = json.loads(rows[0].text if not isinstance(rows, dict)
                                 else json.dumps(rows))
            assert payload.get("error"), "云元数据被放行了 —— 那是 §12 明令不许放宽的闸"

    @pytest.mark.asyncio
    async def test_a_good_spelling_still_stores(self):
        d = await _face({"set": {"allow_private": ["127.0.0.1"]}})
        assert not d.get("error"), d
        assert "in force" in (d.get("message") or "")
        await _face({"unset": ["allow_private"]})
