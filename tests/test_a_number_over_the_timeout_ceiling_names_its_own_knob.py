# -*- coding: utf-8 -*-
"""一颗超过帽的 `timeout=`：谁被拒、拒的那句点名谁、四扇是不是同一位作者写的（评审 R02-093 · R02-096 · R02-097）。

量到的形状（真机 14 发 · `review/.runtime/p1/r110_timeout_gate_sweep.json` +
`r111_write_door_timeout.json` · HEAD 09f43a6 · 9202 pid 27716）：同一件事实四种信封——

- `web_read` / `web_act` / `web_request` 五发：`error` 点名 `Timeout`，`next_action` 逐字却是
  `Correct the argument and call again - no request was made. web_read takes an absolute http(s) URL`
  —— 落到 `_invalid_request_result`（`quality.py:2576`）的**默认 URL 模板**，而那棵函数自己的
  docstring 禁止这句话；五发的门话（`Nothing was …`）全是空集。（R02-096）
- `feed_read` 两发：`error` 是 `feed_read timeout: Timeout exceeds…`，连 `invalid_request:` 前缀都没有。
  （R02-097）
- `web_discover` 一发：唯一讲清预算的那扇（`timeout= … whole budget in milliseconds: 1-120000`）。
- `web_crawl` 两发：门口**不验**，数字走进引擎，交回 `1/1 pages failed with network errors (timeout)` +
  `Retry once with a longer timeout` —— 一颗永远不可执行的建议。（R02-093）

判据按**形状**钉，不按参数名一颗颗枚举：`error` 点名了哪颗参数，`next_action` 就得点名同一颗；
非 URL 类拒绝里不许出现 `takes an absolute http(s) URL`（`tests/test_bug_report2_regressions.py:850-868`
早为 `max_content_chars`/`offset` 钉过同一支，这一族只是没被覆盖面罩到）。
"""
import asyncio
import inspect
import json
import re

import pytest

from dhole_mcp import security as security_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

DEAD = "http://example.invalid/"
CEILING = "Timeout exceeds maximum"
URL_BOILERPLATE = "takes an absolute http(s) URL"
READ_CLAUSE = "Nothing was fetched."
WRITE_CLAUSE = "Nothing was sent."
ACT_CLAUSE = "Nothing was run."


async def _wire(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    async with InMemoryTransport(MasterFetchServer()._mcp_server()) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = "".join(getattr(b, "text", "") for b in res.content)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"_not_json": text[:400]}


def _call(tool, args):
    return asyncio.run(_wire(tool, args))


# 四扇收这颗参数的门（`web_search`/`doc_parse` 不收，另有一格反证钉着它们不该被卷进来）
CEILING_LEGS = [
    pytest.param("web_read", {"url": DEAD, "timeout": 999999}, READ_CLAUSE, id="web_read 999999"),
    pytest.param("web_read", {"url": DEAD, "timeout": 120001}, READ_CLAUSE, id="web_read 刚过帽"),
    pytest.param("web_act", {"url": DEAD, "actions": [{"click": {"selector": "b"}}],
                             "timeout": 999999}, ACT_CLAUSE, id="web_act"),
    pytest.param("web_request", {"url": DEAD, "method": "POST", "body": {"a": 1},
                                 "confirm": True, "timeout": 999999}, WRITE_CLAUSE,
                 id="web_request POST"),
    pytest.param("web_discover", {"url": DEAD, "timeout": 999999}, READ_CLAUSE, id="web_discover"),
    pytest.param("web_crawl", {"url": DEAD, "timeout": 999999}, READ_CLAUSE, id="web_crawl"),
    # 同一颗预算的第二族成因（`r113_feed_prefix.json`：0 与 -5 在三扇上撞出的形状与超帽逐字相同 ——
    # read 念 URL 样板、discover 具名拒、crawl 怪网络），所以判据按区间钉，不按方向钉。
    pytest.param("web_read", {"url": DEAD, "timeout": 0}, READ_CLAUSE, id="web_read 非正数"),
    pytest.param("web_discover", {"url": DEAD, "timeout": 0}, READ_CLAUSE, id="web_discover 0"),
    pytest.param("web_crawl", {"url": DEAD, "timeout": 0}, READ_CLAUSE, id="web_crawl 0"),
]


class TestTheCeilingNamesItsOwnKnob:
    """每一条超帽拒绝都要点名 `timeout`、说本门的话、并且不念 URL 那句样板。"""

    @pytest.mark.parametrize("tool,args,clause", CEILING_LEGS)
    def test_the_way_out_points_at_the_knob_that_was_wrong(self, tool, args, clause):
        p = _call(tool, args)
        err, way = p.get("error") or "", p.get("next_action") or ""
        # 成因有两种（超帽 / 非正数），识别针由产品那**一份定义**给出 ——
        # 这里不写死 "Timeout exceeds maximum"，否则这一格只在超帽那一支上有效（覆盖面按方向枚举，
        # 正是 R02-096 那把的病根）。
        assert any(n in err for n in security_mod.TIMEOUT_REFUSAL_NEEDLES), p
        assert "timeout" in way.lower(), f"出路没点名那颗参数: {way}"
        assert URL_BOILERPLATE not in way, f"出路念的是 URL 样板: {way}"

    @pytest.mark.parametrize("tool,args,clause", CEILING_LEGS)
    def test_the_door_still_declares_itself(self, tool, args, clause):
        p = _call(tool, args)
        texts = f"{p.get('error')} {p.get('next_action')}"
        assert clause in texts, texts
        assert texts.count(clause) == 1, f"同一句说了两遍: {texts}"

    @pytest.mark.parametrize("tool,args,clause", CEILING_LEGS)
    def test_the_refusal_travels_the_standard_envelope(self, tool, args, clause):
        """`error` 的前缀也是契约：宿主按 `invalid_request:` 归类失败（R02-097 那支漂了的前缀）。

        两种信封各有自己的"什么都没发生"格子：读正文那扇看 `status=0`，爬行/地图那两扇没有
        `status`，看的是 `pages` 空 + `pages_crawled=0`。按信封判，不要拿一颗模型的字段去问另一颗
        （那一格原本会永远红/永远绿，取决于 `p.get` 的默认值）。
        """
        p = _call(tool, args)
        assert str(p.get("error", "")).startswith("invalid_request:"), p
        if "pages" in p:
            assert p["pages"] == [] and p.get("pages_crawled") == 0, p
        else:
            assert p.get("status") == 0, p

    def test_the_crawl_stops_at_the_gate_and_does_not_blame_the_site(self):
        """R02-093 那一支的形状：门口不验时，页级汇总把超帽说成「站点慢、抬大 timeout 重试」。

        修后这一发要停在门口：一个包都没付（`pages` 空、`pages_crawled=0`），
        且那句话不能出现在出路里。这是**形状**判据，不是把网络错误一律禁掉 ——
        真的网络慢仍然会说它自己的话（`_no_request` 那类断言归 `test_bug_report2` 管）。
        """
        p = _call("web_crawl", {"url": DEAD, "timeout": 999999})
        texts = json.dumps(p, ensure_ascii=False)
        assert CEILING in (p.get("error") or ""), texts[:300]
        assert p.get("pages") == [] and p.get("pages_crawled") == 0, texts[:300]
        for phrase in ("network errors", "site may be slow", "switch sources",
                       "longer timeout"):
            assert phrase not in texts, f"还在怪站点: {phrase} · {texts[:300]}"

    @pytest.mark.parametrize("tool,args,clause", [
        pytest.param("feed_read", {"urls": [DEAD], "timeout": 999999}, READ_CLAUSE,
                     id="feed_read 的门话与前缀"),
    ])
    def test_feed_read_speaks_the_same_envelope(self, tool, args, clause):
        """`feed_read timeout: …` 那种漂了的前缀（R02-097）：宿主按 `invalid_request:` 归类。"""
        p = _call(tool, args)
        way = p.get("next_action") or ""
        assert str(p.get("error", "")).startswith("invalid_request:"), p
        assert "timeout" in way.lower(), way
        assert clause in f"{p.get('error')} {way}", p
        # 这颗错讲的是数字，不是那颗 `urls`：用法模板不能顶替它（与 URL 样板同一条纪律）
        assert "1-50 absolute" not in way, way

    @pytest.mark.parametrize("args", [
        pytest.param({"urls": [f"{DEAD}f{i}" for i in range(51)]}, id="51 颗 urls"),
        pytest.param({"urls": []}, id="一颗也没给"),
    ])
    def test_the_other_feed_read_refusals_travel_the_same_envelope(self, args):
        """同一封里另外两颗 ValueError 也没带前缀（`r113_feed_prefix.json` 实测两发）。

        判据与上面那颗同一条：这一扇被拒的回执要能被 `invalid_request:` 认出来、要说本门的话、
        出路要点名 `urls` 这一颗。不顺手改判据以外的东西。
        """
        p = _call("feed_read", args)
        way = p.get("next_action") or ""
        assert str(p.get("error", "")).startswith("invalid_request:"), p
        assert READ_CLAUSE in f"{p.get('error')} {way}", p
        assert "urls" in way.lower(), way


class TestOneAuthorForTheCeiling:
    """这句话只许有一位作者：四扇共用 security 里那份常量，门话按门装配。"""

    def _literals(self, code, doc=None) -> list:
        found = []
        for const in code.co_consts:
            if isinstance(const, str):
                if const != doc:
                    found.append(const)
            elif hasattr(const, "co_consts"):
                found.extend(self._literals(const))
        return found

    def _callables(self, mod):
        """类方法也要数：上一把它派生的那支尺只数模块级函数，于是它**永远绿**（㉞ 第四次撞这条）。"""
        for _, member in inspect.getmembers(mod, inspect.isfunction):
            yield member
        for _, klass in inspect.getmembers(mod, inspect.isclass):
            for _, meth in inspect.getmembers(klass, inspect.isfunction):
                yield meth

    def test_the_ceiling_sentence_has_one_author(self):
        """按常量池数：把预算区间写全的那句话，跨模块只许住在 `security` 那一颗常量里。

        `from … import TIMEOUT_CEILING_RECOVERY` 会把同一个**对象**挂到 server 的命名空间里，
        那不是第二位作者 —— 判据数的是"又写了一遍"，所以按对象身份跳过转发名（否则这一格会永远红，
        而永远红的格子与永远绿的格子一样没用）。
        """
        forwarded = {id(v) for v in vars(security_mod).values() if isinstance(v, str)}
        authors = []
        for fn in self._callables(server_mod):
            for const in self._literals(fn.__code__, fn.__doc__):
                if re.search(r"whole budget in milliseconds|1-120000", const):
                    authors.append((server_mod.__name__, fn.__qualname__, const[:70]))
        for name, val in vars(server_mod).items():
            if isinstance(val, str) and id(val) not in forwarded and \
                    re.search(r"whole budget in milliseconds|1-120000", val):
                authors.append((server_mod.__name__, name, val[:70]))
        assert authors == [], f"预算那句话还有别处自己写: {authors}"

    def test_the_ruler_finds_the_pre_fix_author(self):
        """反证：同一把尺在**修前那处**要数得到人 —— 数不到就是尺子瞎了，不是产品干净了。"""
        pre_fix = ("timeout= is this call's whole budget in milliseconds: 1-120000. "
                   "On a sitemap map it is also the clock that one index request runs on.")
        assert re.search(r"whole budget in milliseconds|1-120000", pre_fix)

    def test_the_needle_is_the_constant_the_handlers_read(self):
        """识别针只许一份定义（与 `RESOLVER_GUARD_NEEDLE` / `PRIVATE_ADDRESS_REFUSAL_PREFIXES` 同一惯例）。"""
        assert hasattr(security_mod, "TIMEOUT_REFUSAL_NEEDLES"), "缺一组共享的识别针"
        needles = security_mod.TIMEOUT_REFUSAL_NEEDLES
        assert CEILING in " ".join(needles), needles
        assert any("positive" in n for n in needles), needles
        assert hasattr(security_mod, "TIMEOUT_CEILING_RECOVERY"), "缺一份共享的出路"
        # 出路要点名那颗参数本身，且不念 URL 样板（R02-096 的病根）
        rec = security_mod.TIMEOUT_CEILING_RECOVERY
        assert "timeout=" in rec and URL_BOILERPLATE not in rec, rec

    def test_the_default_url_template_is_still_there_for_url_errors(self):
        """反证：这一把不许把默认模板打死 —— URL 自己写坏时那句话仍然是对的出路。"""
        p = _call("web_read", {"url": "ftp://example.com/"})
        assert URL_BOILERPLATE in (p.get("next_action") or ""), p


class TestTheDoorsThatDoNotTakeIt:
    """不收这颗参数的两扇不能被卷进来（它们撞的是陌生参数名那一层）。"""

    @pytest.mark.parametrize("tool,args", [
        pytest.param("web_search", {"query": "widgets", "timeout": 999999}, id="web_search"),
        pytest.param("doc_parse", {"file_path": "none.pdf", "timeout": 999999}, id="doc_parse"),
    ])
    def test_a_door_without_the_knob_says_so(self, tool, args):
        p = _call(tool, args)
        assert "Unsupported argument(s)" in (p.get("error") or ""), p
        assert CEILING not in (p.get("error") or ""), p
