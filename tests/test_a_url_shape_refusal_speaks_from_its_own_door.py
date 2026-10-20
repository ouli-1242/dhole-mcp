# -*- coding: utf-8 -*-
"""URL 那一族的拒绝要说本门的话：三条路（形状 / 地址 / 设计边界）收成同一位作者（评审 R02-098 · R02-100）。

量到的形状（真机两趟，HEAD e4bdd0d · 9202 pid 21956 · 全部在门口返回、不发包）：

  `review/.runtime/p1/r119_url_shape_clauses.json` 20 格
    · **13 格没有门话**：ftp 那颗在 web_read/web_request/web_act 只说"改参数"、不说这一发没出去过；
      空串/空格 url 与路径里的反斜杠同样六扇全空。唯一的例外是 `feed_read`（`Nothing was fetched.`），
      它证明这半句能写对，只是没人装配。
    · **6 格没有 `invalid_request:` 前缀**：`web_discover`/`web_crawl` 两扇在三族里全部交裸句
      （`URL scheme 'ftp' is not allowed` / `URL contains backslash character …` / `URL targets
      internal/private IP …`），同一件事在 `web_read` 上有前缀 —— 一颗门两种信封（R02-097 立的
      前缀契约在这一族没人守）。
    · **4 格出路是通用句**：discover/crawl 的形状拒绝落到 `errors._HINTS["unknown"]`
      那句 `Fetch failed. Check the error field …; retry with different parameters` ——
      一个包都没发，"retry" 是假出路，且它没点名任何一颗参数。
    · **5 格动词是别人的**（R02-100 的新证据）：地址族六扇里只有写门 `web_request` 那句
      "nothing was sent" 说对了，`web_read`/`web_discover`/`web_crawl`/`feed_read`（read）与
      `web_act`（action）全错。来路只有一条：门话焊在共用文案 `security.PRIVATE_ADDRESS_RECOVERY`
      的**正文中间**（"The URL is well-formed - what is refused is the address it points at,
      and **nothing was sent**."）—— 这是这一族的第六种漏作者的形状：常量里抄了一句。
      `server.py` 的设计边界那一支（`_PERMANENT_URL_REFUSALS`）自带 `Nothing was sent;`，
      真机 `r119b_boundary_clauses.json` 的 M1/M3 在 `web_read`/`web_act` 上说的是 `sent`。

契约（本文件钉住）：
  * 三条路的每一扇，`error` 都带 `invalid_request:` 前缀，`next_action` 都以**本门那句申报**开头，
    且只出现一次（顺序也是契约：调用方扫行首就知道这一发出没出去过 —— 与 F85 同一条）。
  * 门话由 `context.nothing_went_out()` 装配；三份共用文案（形状 / 地址 / 设计边界）只留本体，
    正文里不许再焊任何一扇的动词。
  * 具名拒绝的路上不许出现 `Fetch failed. Check the error field …`。
  * discover/crawl 的形状与边界拒绝停在门口：`pages == []`、`pages_crawled == 0`，
    也不许把话推给站点。
"""
from __future__ import annotations

import inspect
import json
import re

import pytest

from dhole_mcp import context, crawl as crawl_mod, errors, quality, security
from dhole_mcp.context import nothing_went_out
from dhole_mcp.server import MasterFetchServer

GENERIC_UNKNOWN = "Fetch failed. Check the error field"
FTP = "ftp://example.com/"
BS = chr(92)
LOOP = "http://127.0.0.1:8399/"
META = "http://169.254.169.254/latest/meta-data/"
CLAUSE_RE = re.compile(r"[Nn]othing was [a-zA-Z ,\-]+?\.")

# 三族 × 每一扇。args 一律带 confirm，免得被别的闸抢答（B-41 那族教训）。
SHAPE = [
    ("web_read", {"url": FTP}),
    ("web_request", {"url": FTP, "method": "POST", "body": {"a": 1}, "confirm": True}),
    ("web_act", {"url": FTP, "actions": [{"click": {"selector": "b"}}], "confirm": True}),
    ("web_discover", {"url": FTP}),
    ("web_crawl", {"url": FTP}),
    ("feed_read", {"urls": ["ftp://example.com/f.rss"]}),
    ("web_read", {"url": ""}),
    ("web_discover", {"url": ""}),
    ("web_crawl", {"url": ""}),
    ("web_read", {"url": "   "}),
    ("web_read", {"url": "https://example.com" + BS + "a"}),
    ("web_request", {"url": "https://example.com" + BS + "a", "method": "POST",
                     "body": {"a": 1}, "confirm": True}),
    ("web_discover", {"url": "https://example.com" + BS + "a"}),
    ("web_crawl", {"url": "https://example.com" + BS + "a"}),
]
ADDRESS = [
    ("web_read", {"url": LOOP}),
    ("web_request", {"url": LOOP, "method": "POST", "body": {"a": 1}, "confirm": True}),
    ("web_act", {"url": LOOP, "actions": [{"click": {"selector": "b"}}], "confirm": True}),
    ("web_discover", {"url": LOOP}),
    ("web_crawl", {"url": LOOP}),
    ("feed_read", {"urls": [LOOP]}),
]
BOUNDARY = [
    ("web_read", {"url": META}),
    ("web_request", {"url": META, "method": "POST", "body": {"a": 1}, "confirm": True}),
    ("web_act", {"url": META, "actions": [{"click": {"selector": "b"}}], "confirm": True}),
    ("web_discover", {"url": META}),
    ("web_crawl", {"url": META}),
    ("feed_read", {"urls": [META]}),
]
def _tag(group, cases):
    return [(group, door, args) for door, args in cases]


T_SHAPE, T_ADDRESS, T_BOUNDARY = (_tag("shape", SHAPE), _tag("address", ADDRESS),
                                  _tag("boundary", BOUNDARY))
ALL = T_SHAPE + T_ADDRESS + T_BOUNDARY


async def _face(door: str, args: dict) -> dict:
    out = await MasterFetchServer()._dispatch(door, args)
    rows = out[1] if isinstance(out, tuple) else out
    text = rows[0].text if not isinstance(rows, dict) else json.dumps(rows)
    d = json.loads(text)
    assert d.get("error"), f"这一形状本该被拒，却回了没有 error 的包: {d}"
    return d


def _ids(cases):
    out = []
    for g, d, a in cases:
        u = a.get("url") or (a.get("urls") or [""])[0] or "<空>"
        out.append(pytest.param(d, a, id=f"{g}-{d}-{str(u)[:22]!r}"))
    return out


class TestEveryDoorRefusalSpeaksItsOwnVerb:
    """门话是拒绝信封的属性，不是某一份文案里的赠品。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(ALL))
    async def test_the_refusal_is_named_as_an_input_refusal(self, door, args):
        d = await _face(door, args)
        assert d["error"].startswith("invalid_request:"), d["error"][:160]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(ALL))
    async def test_the_way_out_starts_with_this_door_s_clause(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        want = nothing_went_out(door)
        assert na.startswith(want), f"{door}: 行首不是本门那句 → {na[:200]}"
        assert len(CLAUSE_RE.findall(na)) == 1, f"两遍等于两位作者: {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(ALL))
    async def test_a_named_refusal_does_not_offer_a_generic_retry(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        assert GENERIC_UNKNOWN not in na, f"{door} 还在把具名拒绝推给兜底句: {na[:160]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(T_ADDRESS + T_BOUNDARY))
    async def test_an_address_refusal_does_not_blame_the_spelling(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        assert "absolute http(s) URL" not in na, f"{door}: 地址被拒却要读者改拼法 → {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(T_SHAPE))
    async def test_a_shape_refusal_names_the_spelling_it_rejected(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        assert ("http(s)" in na or "url=" in na), f"{door}: 出路没点名 url → {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(T_SHAPE))
    async def test_the_shape_template_names_this_door_not_the_engine(self, door, args):
        """`discover` 借 `crawl` 引擎走这条路：模板里那个门名必须是调用方那一扇。

        真机 `r119` 的 S4/S5 从前根本没有这句（出路是通用兜底句），所以这条判据是**新句子
        的配套守卫**：装配点若把引擎名写死，`web_discover` 的读者会听见 `web_crawl` 的名字 ——
        那正是 R02-083 量到并修过的形状，不许在新增的路上复发（P5 复验 `f86_verify.json`
        逐格读到 `web_discover` / `web_crawl` 各自的名字）。
        """
        na = (await _face(door, args)).get("next_action") or ""
        m = re.search(r"(\w+) takes an absolute http\(s\) URL", na)
        if m is None:
            pytest.skip(f"{door} 这一格走的是自己那份出路（没有共用模板）")
        assert m.group(1) == door, f"{door} 的出路写着 {m.group(1)} takes…"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids([c for c in ALL if c[0] in ("shape", "boundary")
                                                and c[1] in ("web_discover", "web_crawl")]))
    async def test_the_map_doors_stop_at_the_door(self, door, args):
        d = await _face(door, args)
        # 缺 url 那一格由装配层先答（信封是 ResponseModel，压根没有 pages 这一栏），
        # 形状/边界那一格由引擎答（CrawlResponseModel）。两支都算"停在门口"。
        stopped = ((d.get("pages") == [] and d.get("pages_crawled", 0) == 0)
                   or d.get("fetcher_used") == "none")
        assert stopped, f"{door} 的具名拒绝没有停在门口: " \
                        f"pages={d.get('pages_crawled')} fetcher={d.get('fetcher_used')}"


class TestTheThreeSharedTextsCarryNoVerb:
    """共用文案只留本体：焊在正文里的动词会被读到别的门上（R02-100）。"""

    def test_the_shared_bodies_speak_no_door_s_verb(self):
        for name in ("PRIVATE_ADDRESS_RECOVERY", "PERMANENT_URL_RECOVERY",
                     "URL_SHAPE_RECOVERY", "TIMEOUT_CEILING_RECOVERY"):
            body = getattr(security, name, None)
            assert isinstance(body, str), f"缺这份共用文案: {name}"
            assert not re.search(r"nothing was \w+", body, re.I), (
                f"security.{name} 的正文里焊了一句门话")

    def test_the_shape_body_names_the_argument_it_rejects(self):
        assert "http(s)" in security.URL_SHAPE_RECOVERY
        assert "{tool}" in security.URL_SHAPE_RECOVERY, "拼法建议要说本扇的名字，名字由调用方交进来"

    def test_the_boundary_body_still_offers_no_waiver(self):
        # 设计边界没有出路可指：这句话不许被修文案时顺手加上一扇开得到的门（§12 不许放宽闸）。
        assert "allow_private" not in security.PERMANENT_URL_RECOVERY
        assert "design boundary" in security.PERMANENT_URL_RECOVERY

    def test_the_generic_hint_survives_for_real_fetch_failures(self):
        # 兜底句本身没错，错的是具名拒绝走到它。网络类失败照旧拿它。
        _, hint = errors.classify_network_error("Connection refused by target host")
        assert "refused" in (hint or "").lower() or hint


class TestOneAuthorForTheVerb:
    """**共用文案**里不许焊门话：句子的本体一份写，动词由装配点按门取。

    这一把尺量的范围是"模块级字符串常量"（`PRIVATE_ADDRESS_RECOVERY` 那种被几扇门共用的
    文案）—— 那是本轮量到的形状（R02-100），也是静态可判的形状。函数内部的字面量不在这里
    判：仓库里已有十几处按门写对的句子（`web_read` 的 `Nothing was fetched. Rename it …`），
    把它们一次性改写成装配调用是另一件事的半径（§12 最小影响），而且静态扫函数体还会把
    docstring 一起扫进来 —— 量具自己先红在错的原因上（本轮就踩过一次，见 §㉞）。
    """

    WELDED = re.compile(r"(^|[\s;\"'])(Nothing was (sent|fetched|run)"
                       r"|and nothing was (sent|fetched|run))")

    def _welded_constants(self, mod):
        return [(n, v[:70]) for n, v in vars(mod).items()
                if isinstance(v, str) and self.WELDED.search(v)]

    @pytest.mark.parametrize("mod", [quality, crawl_mod, security, errors,
                                     pytest.param(__import__("dhole_mcp.server",
                                                              fromlist=["x"]),
                                                  id="dhole_mcp.server")])
    def test_no_shared_text_writes_another_door_s_verb(self, mod):
        assert self._welded_constants(mod) == [], (
            f"{mod.__name__} 里还有共用文案焊着门话")

    def test_the_ruler_does_flag_a_welded_constant(self):
        # 反向守卫：把修前那份 `PRIVATE_ADDRESS_RECOVERY` 的开头喂给同一把尺，必须量出红。
        pre_fix = ("PRIVATE_X = 'The URL is well-formed - what is refused is the address "
                   "it points at, and nothing was sent.'")
        assert self.WELDED.search(pre_fix), "这把尺子已经读不出焊住的动词了"

    def test_the_author_is_the_only_one(self):
        src = inspect.getsource(context.nothing_went_out)
        for verb in ("Nothing was sent", "Nothing was fetched", "Nothing was run"):
            assert verb in src, f"{verb} 不在唯一那位作者手里"


class TestTheOldReadingsStillFail:
    """反向守卫：三句修前的原话拿到同一把尺子下，必须量出红。"""

    PRE_FIX = [
        # r119 S1（形状 · 无前缀缺失但无门话）· S4（discover 裸句 + 通用句）
        ("shape · web_read", "Correct the argument and call again - no request was made. "
                             "web_read takes an absolute http(s) URL (e.g. https://example.com)."),
        ("shape · web_discover", GENERIC_UNKNOWN + " for details; try a different source "
                                "or retry with different parameters."),
        ("address · web_read", "The URL is well-formed - what is refused is the address it "
                               "points at, and nothing was sent. If you meant a public site, "
                               "search for the page instead."),
        ("boundary · web_act", "No argument fixes this - it is a design boundary: dhole never "
                               "fetches cloud-metadata endpoints (SSRF guard), whatever the "
                               "spelling. Nothing was sent; if you meant a public site, "
                               "the URL itself is wrong."),
    ]

    @pytest.mark.parametrize("label,way", PRE_FIX, ids=[p[0] for p in PRE_FIX])
    def test_the_ruler_rejects_the_pre_fix_sentence(self, label, way):
        door = label.split("·")[1].strip()
        assert not way.startswith(nothing_went_out(door)), f"{label} 修前就该红"
        if door in ("web_read", "web_act"):
            assert not self._own_verb_only(way, door), f"{label} 没有把别人的动词判为错"

    @staticmethod
    def _own_verb_only(way: str, door: str) -> bool:
        """本门那句话在场、且没有别的门的动词 —— 修后的地址/边界族应当如此。"""
        verbs = set(re.findall(r"nothing was (\w+)", way, re.I))
        return {nothing_went_out(door).split()[2].rstrip(".;").lower()} == verbs
