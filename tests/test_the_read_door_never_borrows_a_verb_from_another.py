# -*- coding: utf-8 -*-
"""读门上不许有第二套动词，也不许有没人装配的格子（评审 R02-092 · R02-094 · R02-095）。

量到的形状（真机 32 发 · `review/.runtime/p1/r107_resume_family.json` / `r107b_plan_family.json` /
`r107c_timeout_verb.json` / `r107d_read_radius.json` · HEAD dcb1064 · 9202 pid 23792）：

- **借来的动词**（R02-092）：同一扇 `web_discover` 上，R3/R5 交回 `Nothing was read.`、R6 交回
  `Nothing was read from the plan.`、C1/C2 交回**小写** `nothing was read.`，而同扇的 R1/R2/R4/R7/R8
  交回 `Nothing was fetched.`。同一颗写坏的 `timeout=` 在邻扇 `web_crawl`（C3）交回的也是
  `Nothing was fetched.` —— 两扇读门、同一层错、两种话。
- **没人装配的那格**（R02-094）：`web_read` 的类型拒绝被 `server.py:11316` 那条
  `if name != "web_read": raise` 的支路就地接走，出路自己写，`declarations=[]`（D1/D3/D4 三颗参数实测），
  而 `web_crawl` 同一层走 `params._refuse`、带申报（D6 对照）。
- **空掉的那格**（R02-095）：`resume_token` 形状不对那颗的 `next_action` 是空串（R1/R2 实测），
  同族其余几颗都带出路 —— 缺的是这一颗，不是整族。

三条是同一条规则：**「这一发没出去」由一位作者按门类装配；拒绝的信封三格齐全才叫拒绝。**
既有契约不动：`tests/test_bug_report2_regressions.py` 钉着 `web_read` 那颗参数错误的出路里要有
`no request was made` —— 所以这一支是**加**上门话，不是替掉那句话。
"""
import asyncio
import inspect
import json
import re

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.crawl import plan_store
from dhole_mcp.server import MasterFetchServer

READ_CLAUSE = "Nothing was fetched."
DEAD = "http://example.invalid/"
BORROWED = re.compile(r"nothing was read", re.I)
# 全表那一支尺要**只数句子结尾**：`server.py` 的 `_agent_hints` 里有两处长句
# （`… so nothing was fetched and nothing was read -` 与 `nothing was read - the name you
# gave is not where this call looked`）说的是**另一件事**（CDP 没应答的复合成因、陌生参数名的
# 指引），本轮没有一条回执证明它们害到谁 —— 按 §12 / ㉘ 不在这一把改文案，尺子也不许把它们
# 算成战果。三处实测的句子都以句号收尾，就按那个形状数。
SCAN_BORROWED = re.compile(r"nothing was read( from the plan)?\s*\.", re.I)
ANY_CLAUSE = re.compile(r"[Nn]othing was [a-zA-Z ,\-]+?\.", re.I)


def _discover(**kwargs):
    out = asyncio.run(MasterFetchServer().web_discover(**kwargs))
    assert out.error, f"这一形状本该被拒，却回了没有 error 的包: {out}"
    return out


def _texts(out) -> str:
    return f"{out.error or ''} {out.next_action or ''}"


async def _wire(tool: str, args: dict) -> dict:
    """走真接线（`InMemoryTransport`）：类型归一与它那道门只发生在分发那一步。

    直接调方法会绕过 `_coerce_arg_types`，拿到的是引擎内部的 `invalid literal for int()` ——
    那是 R02-091 的半径，不是这一格的判据（同一个理由，F82 那件也用这条通道）。
    """
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
        return {"_not_json": text[:300]}


class TestTheReadDoorSpeaksOneVerb:
    """四颗实测的拒绝都要说本门那句话，且要说一遍。"""

    @pytest.mark.parametrize("kwargs", [
        pytest.param({"url": DEAD, "resume_token": "no-colon", "sitemap": True},
                     id="R3 · 形状不对 + sitemap"),
        pytest.param({"url": DEAD, "resume_token": "deadbeef:0", "sitemap": True},
                     id="R5 · 形状对 + sitemap"),
        pytest.param({"url": DEAD, "timeout": "one"}, id="C1 · timeout 写成字符串"),
        pytest.param({"url": DEAD, "timeout": 999999}, id="C2 · timeout 超帽"),
    ])
    def test_a_refused_discovery_declares_it_the_doors_way(self, kwargs):
        texts = _texts(_discover(**kwargs))
        assert READ_CLAUSE in texts, texts
        assert not BORROWED.search(texts), f"借了别的门的动词: {texts}"

    @pytest.mark.parametrize("kwargs", [
        pytest.param({"url": DEAD, "resume_token": "no-colon", "sitemap": True},
                     id="R3 · 形状不对 + sitemap"),
        pytest.param({"url": DEAD, "timeout": "one"}, id="C1 · timeout 写成字符串"),
    ])
    def test_the_declaration_is_spoken_once(self, kwargs):
        """两遍等于两位作者：拼接处一旦重复，读的人要猜哪一遍是真的。"""
        texts = _texts(_discover(**kwargs))
        assert texts.count(READ_CLAUSE) == 1, texts

    def test_a_bad_path_filter_on_a_real_cursor_speaks_the_doors_verb(self):
        """R6：这一颗要有**真游标**才走得到（`_plan_map_page` 在计划读回之后才校路径式）。

        靶：`r107b_plan_family.json` 的 R6（`6cab92a06242:0` + `path_include=['[']`）交回
        `Nothing was read from the plan.`，同批 R7/R8 交回 `Nothing was fetched.`。
        对照组也钉住：同一颗游标原样再问要**读得到行**，否则这一格只是"根本没计划"。
        """
        plan_id = plan_store([f"{DEAD}a/1", f"{DEAD}a/2"])
        bad = _discover(url=DEAD, resume_token=f"{plan_id}:0", path_include=["["])
        texts = _texts(bad)
        assert READ_CLAUSE in texts, texts
        assert not BORROWED.search(texts), f"借了别的门的动词: {texts}"
        # 对照组：同一颗游标原样再问要**读得到行**（不走 `_discover`，它的前提是"这一发该被拒"）
        good = asyncio.run(MasterFetchServer().web_discover(
            url=DEAD, resume_token=f"{plan_id}:0"))
        assert not good.error and good.pages, f"同一颗游标本该读得到行: {good.error}"


class TestTheCoercionRefusalHasItsOwnDoor:
    """`web_read` 那条只属于自己的支路：出路要带上门话，而既有那句 no request was made 不动。"""

    @pytest.mark.parametrize("bad", [
        pytest.param({"urls": "one"}, id="D1 · 数组写成裸字符串"),
        pytest.param({"offset": "one"}, id="D3 · 整数写成字符串"),
        pytest.param({"timeout": "one"}, id="D4 · 数字写成字符串"),
    ])
    def test_web_read_says_its_own_door(self, bad):
        payload = asyncio.run(_wire("web_read", {"url": DEAD} | bad))
        way = payload.get("next_action") or ""
        texts = f"{payload.get('error')} {way}"
        assert str(payload.get("error", "")).startswith("invalid_request"), texts
        assert READ_CLAUSE in way, f"出路那格没有门话: {texts}"
        assert way.startswith(READ_CLAUSE), f"门话要在行首: {way}"
        assert not BORROWED.search(texts), texts
        # 既有契约（test_bug_report2_regressions 钉的是 max_content_chars 那一颗，同一条支路）
        assert "no request was made" in way.lower(), way

    def test_the_sister_door_already_said_it(self):
        """对照不许被修成"两边各写一遍"：`web_crawl` 同一层早就是装配好的。"""
        payload = asyncio.run(_wire("web_crawl", {"url": DEAD, "max_pages": "one"}))
        texts = f"{payload.get('error')} {payload.get('next_action')}"
        assert str(payload.get("error", "")).startswith("invalid_request"), texts
        assert READ_CLAUSE in texts, texts
        assert not BORROWED.search(texts), texts


class TestEveryRefusalFillsItsOwnCell:
    """空着的 `next_action` 也是一位缺席的作者（R02-095）。"""

    @pytest.mark.parametrize("tool", ["web_crawl", "web_discover"])
    def test_a_malformed_cursor_still_offers_a_way_back(self, tool):
        out = asyncio.run(getattr(MasterFetchServer(), tool)(
            url=DEAD, resume_token="no-colon"))
        texts = _texts(out)
        assert out.error, out
        assert (out.next_action or "").strip(), (
            f"{tool} 这颗拒绝的出路那格是空串 —— 申报写在 error 里不等于出路有人写过")
        # 这一格的申报只在 error 里出现一遍：补的出路只教「怎么拿回一颗真游标」，不重复签一次字
        assert texts.count(READ_CLAUSE) == 1, texts
        assert "next_resume_token" in (out.next_action or ""), out.next_action


class TestOneAuthorPerClause:
    """这三句在源码里只许有一位作者：读门的拒绝不许再抄 `read`。"""

    def _literals(self, code, doc=None) -> list:
        """常量池要**连着嵌套的代码对象**一起数：内联的 lambda/闭包也写得出句子。"""
        found = []
        for const in code.co_consts:
            if isinstance(const, str):
                if const == doc:
                    continue
                found.append(const)
            elif hasattr(const, "co_consts"):
                found.extend(self._literals(const))
        return found

    def test_no_read_door_refusal_authors_a_read_verb(self):
        """按函数的常量池数，不按文件文本数 —— 注释与文档里引用它不算写它（F79 的同一把尺）。

        尺子本身要先能红：这三处全在 `MasterFetchServer` 的**方法**里，所以只数模块级函数
        的那一支从一开始就是永远绿的（本把这趟就是它自己露馅的，改之前先看过它数到 0 处）。
        """
        from dhole_mcp import crawl as crawl_mod
        from dhole_mcp import params as params_mod

        def callables_in(mod):
            for _, member in inspect.getmembers(mod, inspect.isfunction):
                yield member
            for _, klass in inspect.getmembers(mod, inspect.isclass):
                for _, meth in inspect.getmembers(klass, inspect.isfunction):
                    yield meth

        offenders = []
        for mod in (server_mod, params_mod, crawl_mod):
            for fn in callables_in(mod):
                for const in self._literals(fn.__code__, fn.__doc__):
                    if SCAN_BORROWED.search(const):
                        offenders.append((mod.__name__, fn.__qualname__, const[:80]))
            for name, val in vars(mod).items():
                if isinstance(val, str) and SCAN_BORROWED.search(val):
                    offenders.append((mod.__name__, name, val[:80]))
        assert not offenders, f"读门上还有 {len(offenders)} 处自己写 read: {offenders}"

    def test_the_ruler_itself_can_go_red(self):
        """反证：同一把尺拿**修前那三句原文**去量，必须各数到一处（永远绿的断言不算判据）。"""
        for sample in ("… sitemap= would decide nothing here. Nothing was read.",
                       "Nothing was read from the plan. Fix the pattern.",
                       "invalid_request: timeout= Timeout must be a number - nothing was read."):
            assert SCAN_BORROWED.search(sample), sample

    def test_the_words_still_come_from_one_author(self):
        """装配位不动：门话仍然只由 `context.nothing_went_out` 写出来。"""
        from dhole_mcp import context as context_mod
        holders = [n for n, fn in inspect.getmembers(context_mod, inspect.isfunction)
                   if any(READ_CLAUSE in c for c in self._literals(fn.__code__, fn.__doc__))]
        assert holders == ["nothing_went_out"], holders

    def test_the_payload_shape_survives(self):
        """遮蔽与序列化不许被这次改动打坏：拒绝包要能原样变成 wire JSON，且只申报一次。"""
        out = _discover(url=DEAD, timeout="one")
        payload = json.loads(json.dumps(out.model_dump(exclude_none=True), ensure_ascii=False))
        texts = f"{payload.get('error')} {payload.get('next_action')}"
        assert READ_CLAUSE in texts, texts
        assert {c.rstrip(".") for c in ANY_CLAUSE.findall(texts)} == {"Nothing was fetched"}, texts
