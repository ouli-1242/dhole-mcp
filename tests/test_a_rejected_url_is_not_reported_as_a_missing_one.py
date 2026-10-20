# -*- coding: utf-8 -*-
"""同一颗坏 URL 在各扇门上必须是同一位裁判 —— 搜索门那条 find_similar 路今天不是（评审 R02-131）

现场（`review/.runtime/p1/r262_cross_door_guard_matrix.json` · r263 · r264 · r265 · HEAD c511e02 ·
进程内真 SDK 回合 · 每一发都在发包之前返回，`engines_used`/`fetcher_used` 逐条在册可核）：

  1. **跨门分歧（r265，5 种坏形状 × 6 扇收 `url=` 的门）**：守卫是同一位（`security.validate_url`），
     句子的**骨架**也是同一位（族 → 出路那四支在 `crawl.py:1633-1669` 一份写，动词按门取），
     于是 web_read / web_act / web_request / web_crawl / web_discover 五扇交回的都是
     `invalid_request: URL targets internal/private IP (…)` + 本门申报打头的同一段出路；
     `web_search` 那一扇交回的是 **"find_similar requires a url (pass url=, or the URL as query)"** ——
     调用方明明传了 `url=`，这句话否认的是同一封回执里他自己的动作（D5 那条法），而守卫给出的
     真原因在 `search.py:2567` 那句 `except Exception: find_sim_url = ""` 里被吞掉了。
  2. **把手整格缺席（r264 9 张脸 + r265 15 格）**：`web_crawl` / `web_discover` / `web_search` 的
     回执模型（`CrawlResponseModel` / `SearchResponseModel`）根本没有 `diagnosis` 这一格，
     而 `fields.md` 与 `models.py:143` 说的是"**每一扇设计过的失败**都有一颗固定 token"、
     "ABSENT = 干净成功"。R02-129 收过 quality 那两处装配位、R02-130 收过两扇状态门的模型，
     这是同一条轴上剩下的第三条与第四条装配路径。
  3. **表里没有格子（r264）**：`Invalid mode:` / `Invalid engine:` / `Invalid freshness:` /
     `Invalid page:` 这些自家判决句都不在册 —— 补了格子去问表，表也答 `''`（大写开头进不了
     前缀档，首词兜底只认小写），而 `find_similar requires a url` 会被首词兜底**发明**成
     `'find_similar'`（r264 实测 layer=first-word 四条）。

契约（本文件钉住）：
  ① 同一颗坏 URL 在所有收 `url=` 的门上：同一颗 code（`invalid_request`）、同一段出路正文
     （只差本门那句动词）、同一件事实"这一发没出去过"；
  ② 被守卫挡下的 URL **不许**被说成"没传 URL"；什么都没传时才许说那句；
  ③ 每一扇设计过的失败都交出 `diagnosis`，`code` 由判据表给（哨兵换表 ⇒ 全部被扫的脸跟着变），
     且不许是首词兜底发明的词；
  ④ 闸门不因为这一把放松：每种坏形状在每扇上都还是零结果、零发包（这条写在同一把尺里，
     是为了下一位改出路句子的人不能顺手把门也推开）。

`mode` 缺席时 `url=` 被静默忽略那一半**不在本文件钉**（它要走引擎，线上量不到，源码读得到：
`search.py:2565` 只在 find_similar 这支读它）；那是另一颗观察，记在 backlog。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import context as CTX
from dhole_mcp import crawl as CRAWL
from dhole_mcp import quality as Q
from dhole_mcp import search as SEARCH
from dhole_mcp import server as SRV
from dhole_mcp.server import MasterFetchServer

TABLE_CODES = {e[1] for e in Q._DIAGNOSIS_PREFIXES} | {e[1] for e in Q._DIAGNOSIS_CONTAINS}
FIXTURE = "http://127.0.0.1:8399/index.html"

#: 五种必然在发包之前被守卫挡下的 `url=`（全是本机/非法形状，外网一个名字都不查）
BAD_URLS = [
    ("loopback", FIXTURE, "internal/private IP"),
    ("private-ip", "http://10.1.2.3/x", "internal/private IP"),
    ("metadata", "http://169.254.169.254/latest/meta-data", "cloud metadata"),
    ("no-scheme", "not a url", "Only http and https"),
    ("javascript", "javascript:alert(1)", "'javascript' is not allowed"),
]

#: 每扇门的必填锚点：缺了锚点就先撞"缺参数"那面，量到的不是 URL 守卫这一面。
#: 新长出一扇收 `url=` 的门而这里没有它，第 ① 格会点名报错 —— 那是尺具在催下一位作者，不是漏量。
ANCHORS = {"web_read": {}, "web_request": {"method": "POST"},
           "web_act": {"actions": [{"click": "#nope"}]},
           "web_crawl": {}, "web_discover": {}, "feed_read": {},
           "web_search": {"mode": "find_similar"}}

SEARCH_REFUSALS = [
    ("mode 里的陌生词", {"mode": "bogus-mode", "query": "r131"}),
    ("缺词也缺 url", {"query": ""}),
    ("名册里没有的引擎", {"query": "r131", "engines": ["no-such-engine"]}),
    ("freshness 的陌生词", {"query": "r131", "freshness": "nonsense"}),
    ("page 是负数", {"query": "r131", "page": -2}),
    # R02-136 把首词那把尺的名单改成扫出来之后，这三面第一次到场（前四张是 F113 数的）：
    # 两颗是参数拒绝（同一扇门里，别颗交 `invalid_request` 而它们交措辞发明的词），
    # 一颗是 find_similar 读不到源页（那颗**不是**参数拒绝，见下面单独一枚腿）。
    ("find_similar 没交 url", {"mode": "find_similar", "url": ""}),
    ("共识地板踩在下限下", {"query": "r02-139", "fetch_min_consensus": 1}),
    ("共识地板没开取正文", {"query": "r02-139", "fetch_min_consensus": 3}),
]


async def _call(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    text = "".join(getattr(b, "text", "") for b in res.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_prose": text[:400]}


def _err(p: dict) -> str:
    return str(p.get("error") or p.get("_prose") or "")


URL_DOORS = sorted(d for d, args in SRV._TOP_LEVEL_ARGS.items() if "url" in args and d in ANCHORS)


@pytest.mark.parametrize("door", URL_DOORS)
def test_the_url_doors_are_all_here(door):
    """尺具自己的名单：每扇收 `url=` 的门都得有锚点，否则这一族的跨门比较缺一角。"""
    assert door in ANCHORS, door


@pytest.mark.parametrize("door", URL_DOORS)
@pytest.mark.parametrize("shape,url,needle", BAD_URLS)
@pytest.mark.asyncio
async def test_a_rejected_url_is_judged_the_same_way_on_every_door(door, shape, url, needle):
    """判据 ①：同一颗坏 URL 在所有门上同一颗 code、同一段出路正文（只差本门的动词）。"""
    args = {"url": url}
    args.update(ANCHORS[door])
    p = await _call(door, args)
    err = _err(p)
    assert err, (door, shape, sorted(p))
    assert needle in err, (door, shape, err[:160])
    dia = p.get("diagnosis")
    assert isinstance(dia, dict), f"{door}/{shape} 的把手整格缺席：{sorted(p)}"
    assert dia.get("code") == "invalid_request", (door, shape, dia)
    assert dia.get("retryable") is False, (door, shape, dia)
    clause = CTX.nothing_went_out(door)
    na = str(p.get("next_action") or "")
    assert na.startswith(clause), (door, shape, na[:120])
    body = na[len(clause):].strip()
    assert body, (door, shape, na[:120])
    # 出路正文的**第一位作者**是 security 那四段共用文案，门名只进动词；这里比较正文的开头，
    # 因为 URL_SHAPE_RECOVERY 里插了 `{tool}`（那是唯一一处按门换字的共用文案，允许不同）。
    if "Absolute http(s)" in body or "takes an absolute http(s)" in body:
        pass          # 形状族那一句本来就点名本门（`tool=` 一位作者）
    if door != "web_read":
        peer = await _call("web_read", {"url": url})
        peer_body = str(peer.get("next_action") or "")[len(CTX.nothing_went_out("web_read")):].strip()
        same_family = (body.split(".")[0][:40] == peer_body.split(".")[0][:40]
                       or "absolute http(s)" in body.lower())
        assert same_family, (door, shape, body[:120], peer_body[:120])


@pytest.mark.parametrize("door", URL_DOORS)
@pytest.mark.asyncio
async def test_the_gate_still_closes(door):
    """判据 ④：这一把不推门 —— 私网那两种形状在每扇上都一个包都没发。"""
    for url in (FIXTURE, "http://10.1.2.3/x", "http://169.254.169.254/latest/meta-data"):
        args = {"url": url}
        args.update(ANCHORS[door])
        p = await _call(door, args)
        assert _err(p), (door, url, sorted(p))
        assert p.get("engines_used") in (None, [], {}), (door, url, p.get("engines_used"))
        assert p.get("fetcher_used") in (None, "none"), (door, url, p.get("fetcher_used"))
        assert not (p.get("results") or p.get("pages") or p.get("content")
                    or p.get("entries") or p.get("feeds")), (door, url)


@pytest.mark.parametrize("shape,url,needle", BAD_URLS)
@pytest.mark.asyncio
async def test_a_rejected_url_is_not_reported_as_a_missing_one(shape, url, needle):
    """判据 ②：find_similar 收到被守卫挡下的 URL，要说守卫说的原因，不许说"你没传"。"""
    p = await _call("web_search", {"mode": "find_similar", "url": url})
    err = _err(p)
    assert needle in err, (shape, err[:170])
    assert "requires a url" not in err, (shape, "传了的 URL 被说成没传：" + err[:150])


@pytest.mark.asyncio
async def test_a_genuinely_missing_url_still_says_so():
    """正控制：什么都没传时那句真话留着 —— 这一把修的是**吞掉原因**，不是把那句话删了。"""
    p = await _call("web_search", {"mode": "find_similar", "url": ""})
    assert "requires a url" in _err(p), _err(p)[:170]


@pytest.mark.asyncio
async def test_a_source_page_that_cannot_be_read_is_named_by_a_registered_token(monkeypatch):
    """判据 ⑤（R02-137 · 现场 r276）：find_similar 读不到源页那一面，code 不许由措辞决定。

    `fetch_source_for_similar` 交回空 ⇒ 门里那句 `could not fetch the source URL for
    find_similar (blocked or offline).` 是 **dhole 自己写的**句子，从前判据表里没有它的格子，
    上线的 `code` 是小写首词兜底发明的 `could`（那条兜底按 quality.py:2153 的注释是给"消息写在
    dhole 之外"的句子留的）。这一枚钉的是：这颗 token 在册、住在表里，而不是被首词顶替。

    零发包：源页取词被桩住（真请求根本不会发生），重排器加载也桩住（它会去碰磁盘/网络），
    URL 用本机靶站那颗带 `captcha` 字样的页 —— 这正是门里那条 blocked 判据认的形状，
    放开只对本机回环生效（`DHOLE_ALLOW_PRIVATE_HOSTS=127.0.0.1`，与仓库里其余本地量具同一惯例）。
    """
    from unittest.mock import AsyncMock

    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

    async def _no_source(url, **kwargs):
        # 桩交的是**看见的那一件事**（R02-138 的修法形状）：靶站 /captcha 那页命中 blocked。
        return {"title": "", "body": "", "status": 200,
                "reason": "blocked", "detail": ""}

    monkeypatch.setattr(SEARCH, "fetch_source_for_similar", _no_source)
    monkeypatch.setattr(SEARCH, "ensure_reranker", AsyncMock(return_value=None))
    p = await _call("web_search", {"mode": "find_similar",
                                   "url": "http://127.0.0.1:8399/captcha"})
    err = _err(p)
    assert "source page" in err and "find_similar" in err, err[:170]  # R02-137 的本职：句首带着在册的名字，说的是源页这一面
    dia = p.get("diagnosis")
    assert isinstance(dia, dict), f"这面戴 error 没把手：{sorted(p)}"
    assert dia.get("code") != "could", f"code 是首词兜底发明的：{dia}"
    assert dia.get("code") in TABLE_CODES, (dia, sorted(TABLE_CODES))
    assert str(dia.get("recover_with") or "").strip(), dia


@pytest.mark.asyncio
async def test_the_advice_and_the_verdict_agree_on_retrying_that_page(monkeypatch):
    """D5 那一法的建议位形状（R02-137 的另一半）：叫人事先 Retry 的回执不许写 retryable=false。

    现场读数是 `('could', False, '')` 配 `next_action="Retry, or web_read the source URL first…"`
    —— 同一封回执里两只手互相否决。这一枚不规定哪一只是对的（判决归谁由 R02-138 那张账判），
    只钉住两只手不能互相否决。
    """
    from unittest.mock import AsyncMock

    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

    async def _no_source(url, **kwargs):
        # 桩交的是**看见的那一件事**（R02-138 的修法形状）：靶站 /captcha 那页命中 blocked。
        return {"title": "", "body": "", "status": 200,
                "reason": "blocked", "detail": ""}

    monkeypatch.setattr(SEARCH, "fetch_source_for_similar", _no_source)
    monkeypatch.setattr(SEARCH, "ensure_reranker", AsyncMock(return_value=None))
    p = await _call("web_search", {"mode": "find_similar",
                                   "url": "http://127.0.0.1:8399/captcha"})
    na = str(p.get("next_action") or "")
    dia = p.get("diagnosis") or {}
    if "retry" in na.lower():
        assert dia.get("retryable") is True, (na[:120], dia)
    else:
        assert dia.get("retryable") is False, (na[:120], dia)



@pytest.mark.parametrize("label,args", SEARCH_REFUSALS)
@pytest.mark.asyncio
async def test_the_search_doors_own_arg_refusals_hands_over_a_code(label, args):
    """判据 ③：`Invalid <arg>: …` 这一族从此在册（`invalid_request` 一颗 code，名字与别扇同一位）。"""
    p = await _call("web_search", args)
    err = _err(p)
    assert err, (label, sorted(p))
    dia = p.get("diagnosis")
    assert isinstance(dia, dict), f"{label} 的把手整格缺席：{sorted(p)}"
    assert dia.get("code") == "invalid_request", (label, dia)
    assert dia.get("code") in TABLE_CODES, (label, dia)
    assert str(dia.get("recover_with") or "").strip(), (label, dia)
    assert str(dia.get("recover_with")) != str(dia.get("code")), (label, dia)


def test_the_repair_hint_and_the_code_are_one_author_per_fact():
    """出路那一句的作者没换：`_param_repair_hint` 仍按 `Invalid <arg>:` 表点名参数，
    而 code 由判据表给 —— 两件事各有各的作者，不许并成一个（并了就会有人去改 hint 表顺手改 code）。"""
    for prefix in ("Invalid mode:", "Invalid engine:", "Invalid freshness:", "Invalid page:",
                   "Invalid site filter:", "Invalid exclude_sites entry:",
                   "Invalid location:", "Invalid language:"):
        assert any(k[0] == prefix for k in SEARCH._PARAM_REPAIR_HINTS), prefix
        code, retryable, recover = Q.classify_error_text(prefix + " 'x' (whatever)")
        assert code == "invalid_request", (prefix, code)
        assert retryable is False and str(recover).strip(), (prefix, code, recover)


@pytest.mark.asyncio
async def test_the_codes_come_from_the_table_not_from_the_door(monkeypatch):
    """归属锁（F93）：哨兵换掉判据表唯一的读者 ⇒ 被扫的每一张脸跟着变（含 crawl / search 两副回执）。"""
    monkeypatch.setattr(Q, "classify_error_text", lambda err: ("SENTINEL", False, "from the table"))
    faces = [("web_search", {"mode": "bogus-mode", "query": "r131"}),
             ("web_search", {"mode": "find_similar", "url": "http://10.1.2.3/x"}),
             ("web_crawl", {"url": "http://10.1.2.3/x"}),
             ("web_discover", {"url": "not a url"}),
             ("web_read", {"url": "http://10.1.2.3/x"})]
    for tool, args in faces:
        p = await _call(tool, args)
        dia = p.get("diagnosis") or {}
        assert dia.get("code") == "SENTINEL", (tool, args, dia)
        assert str(dia.get("recover_with") or "").startswith("from the table"), (tool, args, dia)


def test_the_crawl_and_search_receipts_have_the_slot():
    """模型前提（与 R02-130 那格同法）：这两副回执从此有这一格，而线上只在有 error 时上线。"""
    assert "diagnosis" in CRAWL.CrawlResponseModel.model_fields
    assert "diagnosis" in SEARCH.SearchResponseModel.model_fields
    # 干净形状不带它：直接造一副没有 error 的回执，按 wire 那一份序列化看键
    from dhole_mcp import wire
    clean = SEARCH.SearchResponseModel(query="r131", results=[], total_results=0)
    assert "diagnosis" not in wire._wire_dump(clean), sorted(wire._wire_dump(clean))
    refused = SEARCH.SearchResponseModel(query="r131", results=[], total_results=0,
                                         error="Invalid mode: 'x' (auto|neural|find_similar)")
    dumped = wire._wire_dump(refused)
    assert dumped.get("diagnosis", {}).get("code") == "invalid_request", dumped
