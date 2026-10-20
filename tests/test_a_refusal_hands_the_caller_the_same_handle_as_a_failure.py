# -*- coding: utf-8 -*-
"""设计过的拒绝必须交出 `diagnosis` —— 手册教的那根把手不许只对一半的失败存在（R02-129 · F110）

现场（`review/.runtime/p1/r246_diagnosis_code_census.json` · r247 · r250 · r251 · HEAD f9c176e ·
pid 26172 · 零外网）：`references/fields.md:67` 写的分支把手是 **`diagnosis.code`**
（"switch on `code` instead of parsing `error`"），并且 `models.py:143` 承诺
「a fixed snake_case token for **every failure dhole designs**」+「ABSENT = a clean success」。
实测却是一只手里没有把手、一只手把拒绝读成成功：

  * A 支（装配位缺席）：**10 扇 ResponseModel 形状的门**各发一颗统一的拒绝脸（谁都不收的参数名），
    `diagnosis` 整格不在 —— 因为 `quality._invalid_request_result` 造完信封只走 `_apply_envelope`，
    从没问过判据表。同一颗 error 原句直接问表，答的是
    `('invalid_request', False, 'fix the named argument')`：表认得这句，缺的是有人去问。
  * B 支（表里没有格子）：`parse` 自家那两条大写开头的句子（`Unsupported file type` /
    `File too large`）进不了前缀表，而首词兜底 `_LEADING_CODE_RE` 只认小写 token（那条本来是给
    **外人写的**句子留的，quality.py:2153 那格 `not a feed` 就是这条兜底发明过一个词的证据）
    ⇒ `_diagnose` 交回 None，整格缺席。

同一封回执里 `content_ok: false` 与「没有 diagnosis」并排站着，就是 D5 那条法在建议位的形状：
两只手互相否决。

契约（本文件钉住；两条边界一并立着）：
  1. 每扇 JSON 门的设计拒绝都带着 `diagnosis`，`code` 由**判据表**给出（`invalid_request`），
     `retryable` 为 false（重发同一串不会改变答案），`recover_with` 非空；
  2. parse 那两族在册：各自的 token 稳定，且不靠首词兜底发明（表里有格子才算）；
  3. 归属锁（F93）：把 `quality._diagnose` 换成哨兵 ⇒ 拒绝信封里那格要跟着变
     —— 在 `_invalid_request_result` 里手写一颗 code 的修法在这一格会红；
  4. 正控制：真成功的响应仍然**没有** `diagnosis`（不许把这一格修成每次都发）；
  5. 邻座：`sys_guide` 按设计说散文（F99/R02-115），它没有这一格是对的，本文件钉住它不被"顺手补齐"。

F114 把第 1 条从**参数层**推到**门自己写的那一句**（R02-133 · 现场 `r271_feed_refusal_faces.json`
+ `r271b_feed_batch_and_timeout.json` · 零外网）：`feed_read` 的 `except ValueError` 那一段不穿
任何模型，直接把四格 dict 交给 `_wire_json`，于是这一扇门**线上 9 面**（没 URL / 空列表 / 全空白 /
超 50 颗 / 形状 / 私网 / 元数据边 / 超时帽）戴着 `error` 却没有把手，而同门的参数层脸
（谁都不收的键名）有把手 —— 同一扇门按第 1 条只该有一种读法。分工照旧：URL 那一族在各门上的
一致性由 `test_a_rejected_url_is_not_reported_as_a_missing_one.py` 钉，本文件只管"有 error 就要
交出把手"这一条，所以新加的几枚都只问把手在不在、由谁写。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import quality as Q
from dhole_mcp.server import MasterFetchServer

TMP_DOC = "AAA1 BBB2 CCC3 DDD4 EEE5\n" + "u" * 60

#: 统一的拒绝脸：一颗谁都不收的参数名 ⇒ 全部走参数层 ⇒ `_invalid_request_result`
JSON_DOORS = [
    ("doc_parse", lambda p: {"file_path": p, "not_an_argument": 1}),
    ("web_read", lambda p: {"url": "https://example.invalid/x", "not_an_argument": 1}),
    ("web_search", lambda p: {"query": "r02-129", "not_an_argument": 1}),
    ("feed_read", lambda p: {"url": "https://example.invalid/feed", "not_an_argument": 1}),
    ("web_crawl", lambda p: {"url": "https://example.invalid/", "not_an_argument": 1}),
    ("web_discover", lambda p: {"url": "https://example.invalid/", "not_an_argument": 1}),
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


@pytest.fixture(scope="module")
def doc_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("refusal-handle") / "body.txt"
    p.write_text(TMP_DOC, encoding="utf-8")
    return str(p)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,make", JSON_DOORS)
async def test_a_refusal_from_any_json_door_hands_over_a_code(tool, make, doc_path):
    """A 支：设计过的拒绝要带着把手来 —— `code` / `retryable` / `recover_with` 三件都在。"""
    payload = await _call(tool, make(doc_path))
    err = str(payload.get("error") or "")
    assert err, payload
    assert "diagnosis" in payload, (tool, f"把手整格缺席：{sorted(payload)}")
    dia = payload["diagnosis"]
    assert dia.get("code"), (tool, dia)
    assert dia.get("retryable") is False, (tool, f"重发同一串不会改变答案：{dia}")
    assert str(dia.get("recover_with") or "").strip(), (tool, dia)


@pytest.mark.asyncio
async def test_the_prose_door_still_answers_in_prose(doc_path):
    """邻座：`sys_guide` 按设计不说 JSON（R02-115）—— 它没有这一格是对的，别顺手补。"""
    payload = await _call("sys_guide", {"topic": "no-such-topic-xyz", "not_an_argument": 1})
    assert "diagnosis" not in payload, payload
    assert payload.get("_prose"), f"这扇的门面是散文：{sorted(payload)}"


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_attr,expect_code", [
    ("mp3_path", "unsupported_file_type"),
    ("big_path", "file_too_large"),
])
async def test_parses_own_two_capital_sentences_have_a_token(fixture_attr, expect_code, request):
    """B 支：`parse` 那两句自家话各自有一颗在册的 token（不是首词兜底发明的词）。"""
    path = request.getfixturevalue(fixture_attr)
    payload = await _call("doc_parse", {"file_path": path})
    assert str(payload.get("error") or ""), payload
    dia = payload.get("diagnosis")
    assert isinstance(dia, dict), f"{fixture_attr} 的把手整格缺席: {sorted(payload)}"
    assert dia.get("code") == expect_code, dia
    assert dia.get("retryable") is False, dia


def _mp3(tmp_path_factory):
    p = tmp_path_factory.mktemp("handle-b") / "sound.mp3"
    p.write_text("AAA1 BBB2\n", encoding="utf-8")
    return str(p)


def _big(tmp_path_factory):
    from dhole_mcp import parse as PARSE
    p = tmp_path_factory.mktemp("handle-b-big") / "huge.txt"
    with open(p, "wb") as fh:
        fh.truncate(PARSE.MAX_PARSE_FILE_SIZE + 1024 * 1024)
    return str(p)


mp3_path = pytest.fixture(scope="module")(_mp3)
big_path = pytest.fixture(scope="module")(_big)


@pytest.mark.asyncio
async def test_the_code_is_written_by_the_table_not_by_the_envelope(doc_path, monkeypatch):
    """归属锁（F93）：哨兵换上去，拒绝信封里那格要跟着变 —— 手写一颗 code 的修法会红。"""
    sentinel = {"code": "SENTINEL", "retryable": True, "recover_with": "from the sentinel"}
    original = Q._diagnose

    def fake(result):
        return dict(sentinel)

    monkeypatch.setattr(Q, "_diagnose", fake)
    try:
        payload = await _call("doc_parse", {"file_path": doc_path, "not_an_argument": 1})
    finally:
        Q._diagnose = original
    assert payload.get("diagnosis") == sentinel, (
        f"拒绝信封没在问那位作者：{payload.get('diagnosis')}")


def test_the_table_covers_the_two_parse_sentences_without_the_fallback():
    """表锁：两颗 token 是**册**里的，不靠 `_LEADING_CODE_RE` 兜底（兜底是给外人的句子留的）。"""
    prefixes = {entry[0] for entry in Q._DIAGNOSIS_PREFIXES}
    assert any(p in "Unsupported file type" for p in prefixes), sorted(prefixes)
    assert any(p in "File too large" for p in prefixes), sorted(prefixes)
    for sentence in ("Unsupported file type '.mp3'. Supported: .txt",
                     "File too large (51.0 MB). Maximum supported size is 50 MB."):
        code, retryable, recover = Q.classify_error_text(sentence)
        assert code, sentence[:40]
        assert retryable is False and str(recover).strip(), (sentence[:40], code, recover)


class _StubVerdict:
    """`_robots_blocked_result` 只读 `robots_url` 这一颗（其余都不在它手上）。"""
    robots_url = "http://127.0.0.1:8399/robots.txt"


def test_the_robots_preflight_refusal_hands_the_same_handle_over():
    """第三位作者（修的时候在同一个文件里读到的）：robots 预检拒绝也是「什么都没发出去」。

    这一面线上不好造（本机那台宿主带着 `DHOLE_IGNORE_ROBOTS=1` 跑，本地靶站的规则因此不参与），
    所以钉在**装配位**上：builder 交出的信封要带着表写的那颗 token。
    """
    result = Q._robots_blocked_result("http://127.0.0.1:8399/private/secret", _StubVerdict())
    dia = result.diagnosis
    assert isinstance(dia, dict), f"预检拒绝没交出把手：{result.error!r}"
    assert dia.get("code") == "robots_disallowed", dia
    assert dia.get("retryable") is False, dia


@pytest.mark.asyncio
async def test_a_clean_success_still_carries_no_handle(tmp_path):
    """正控制：不许把这一格修成「每次都发」—— 手册写的 ABSENT = 干净成功要照旧成立。"""
    p = tmp_path / "readable.txt"
    p.write_text(TMP_DOC, encoding="utf-8")
    payload = await _call("doc_parse", {"file_path": str(p)})
    assert not payload.get("error"), payload.get("error")
    assert payload.get("content_ok") is True, payload
    assert "diagnosis" not in payload, sorted(payload)


# ─── R02-133 · 门自己写的那一句（F114）───────────────────────────────
#
# 上面那六枚钉的是**参数层**（`_invalid_request_result` → `ResponseModel` 家族，F110 把装配位
# 并到 `_attach_diagnosis`）。这一族是**门里自己 raise 的那几句**：`feed_read` 的
# `except ValueError` 不穿任何模型，把四格 dict 直接交给 `_wire_json`。r271/r271b 数到 9 面，
# 每一面都写着「一个包都没发出去」，每一面都没有把手。全部选在**取词就拒**的面：零外网、
# 零靶站依赖（那颗 51 串的域名永远不被解析，因为条数校验在前一步就把这一发接走了）。
FEED_OWN_FACES = [
    ("no argument at all", {}),
    ("empty list", {"urls": []}),
    ("all blank entries", {"urls": ["", "   "]}),
    ("more than 50 urls", {"urls": [f"http://h{i}.example.invalid/f" for i in range(51)]}),
    ("url shape", {"urls": ["not a url"]}),
    ("private address", {"urls": ["http://10.0.0.1/feed"]}),
    ("cloud metadata edge", {"urls": ["http://169.254.169.254/latest/meta-data/"]}),
]

#: 门里那几句在 wire 上该交回的形状（F114 只加把手那一格，其余四格一格不动 —— 少一格是
#: 回执变瘦，多一格是别的门的衣服，两条都是 R02-103 判过的那种漂移）
FEED_REFUSAL_KEYS = ("diagnosis", "error", "feeds", "next_action", "summary")


@pytest.mark.asyncio
@pytest.mark.parametrize("label,args", FEED_OWN_FACES)
async def test_the_feed_door_answers_its_own_refusal_with_a_code(label, args):
    """R02-133 的本体：戴 `error` 的回执自己认领把手，无论这句话出自参数层还是出自门里。"""
    payload = await _call("feed_read", args)
    err = str(payload.get("error") or "")
    assert err.startswith("invalid_request:"), (label, err[:140])
    dia = payload.get("diagnosis")
    assert isinstance(dia, dict), f"{label} 戴着 error 没把手：{sorted(payload)}"
    assert dia.get("code"), (label, dia)
    assert dia.get("retryable") is False, (label, f"重发同一串不会改变答案：{dia}")
    assert str(dia.get("recover_with") or "").strip(), (label, dia)


@pytest.mark.asyncio
async def test_the_feed_door_refusals_keep_one_shape(doc_path):
    """形状锁（R02-103 那把尺的这一格）：门里那九面线上交回同一副形状，且只多把手那一格。"""
    shapes = set()
    for label, args in FEED_OWN_FACES:
        payload = await _call("feed_read", args)
        shapes.add(tuple(sorted(payload)))
    assert shapes == {FEED_REFUSAL_KEYS}, shapes


@pytest.mark.asyncio
async def test_the_timeout_cap_face_of_the_same_door_carries_a_code(monkeypatch):
    """超时帽那一面单独一枚：它要先把这一发的地址放行才走得到（r271 的探针就打歪在这里）。

    `DHOLE_ALLOW_PRIVATE_HOSTS=127.0.0.1` 只放开本机回环，且 `validate_timeout` 在**任何取文档
    之前** —— 这一面同样一个包都没发出。
    """
    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")
    payload = await _call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                                        "timeout": 10 ** 12})
    err = str(payload.get("error") or "")
    assert "timeout" in err.lower(), err[:160]
    assert sorted(payload) == list(FEED_REFUSAL_KEYS), sorted(payload)
    assert (payload.get("diagnosis") or {}).get("code"), payload


@pytest.mark.asyncio
async def test_the_same_refused_address_is_judged_once_across_doors():
    """R02-121 那一族的跨门检查：同一件事在 feed 门与 crawl 门要听到同一颗 token。"""
    feed = await _call("feed_read", {"urls": ["http://10.0.0.1/feed"]})
    crawl = await _call("web_crawl", {"url": "http://10.0.0.1/"})
    assert (feed.get("diagnosis") or {}).get("code") == \
        (crawl.get("diagnosis") or {}).get("code"), (
        f"两位裁判：feed={feed.get('diagnosis')} crawl={crawl.get('diagnosis')}")


@pytest.mark.asyncio
async def test_the_feed_door_asks_the_table_not_its_own_envelope():
    """归属锁（F93）：哨兵换掉 `row_diagnosis` ⇒ 门里那句的把手跟着变 —— 手写一颗 code 会红。"""
    sentinel = {"code": "SENTINEL", "retryable": True, "recover_with": "from the sentinel"}
    original = Q.row_diagnosis
    Q.row_diagnosis = lambda error: dict(sentinel)
    try:
        payload = await _call("feed_read", {"urls": []})
    finally:
        Q.row_diagnosis = original
    assert payload.get("diagnosis") == sentinel, (
        f"门里那句的把手不是问表要的：{payload.get('diagnosis')}")


def test_the_feed_shell_ships_a_handle_only_when_it_owns_an_error():
    """正控制 + 前提：这副壳没 error 时整格缺席（不是 null），有 error 时才由表写。"""
    from dhole_mcp import wire as W
    from dhole_mcp.models import FeedRefusalModel

    quiet = W._wire_dump(FeedRefusalModel(feeds=[], error="", summary="", next_action=""))
    assert "diagnosis" not in quiet, sorted(quiet)
    refused = W._wire_dump(FeedRefusalModel(
        feeds=[], error="invalid_request: feed_read requires at least one URL",
        summary="no request went out", next_action="Pass urls=[...]"))
    assert (refused.get("diagnosis") or {}).get("code") == "invalid_request", refused
    assert refused.get("feeds") == [], f"`feeds` 这一格从前在，不许因为换壳而瘦掉：{refused}"



# ─── F116 · 行级的那一副回执也要认领把手（R02-142 · 现场 r282 / r284）──────────
#
# r282 并排读了同一颗 404：`web_read` 的信封带 `code=http_error_404`，`web_crawl` 的**行**
# 带着**同一句** `http_error_404: server returned error status`（那一句由 `quality` 的在册
# 作者写的， crawling 只是转手），可 `CrawlPage.model_fields` 里根本没有 `diagnosis` 这一格
# —— 于是这一行有判决的原料却没有把手。r284 顺出另一半：这一族的句子在别处还有第二、第三位
# 作者（`feed._grab` 的裸 `HTTP <status>`、`feed too large (…)`、`since …`），它们的 code
# 由首词兜底发明或整格缺席。两半合起来是同一条法：**有 `error` 就要交出把手，且由同一位读者
# 装配**（`models.RefusalHandle` 那一副机制，与 feed 的行、状态门的格子同一处）。

def test_a_crawl_row_that_failed_wears_the_handle_its_error_names():
    """爬行的行带着在册句子，就必须带着由那句子的读者写的把手（不许转手时丢掉）。"""
    from dhole_mcp.crawl import CrawlPage
    from dhole_mcp.quality import status_error_sentence

    row = CrawlPage(url="https://origin.test/gone", status=404, content_ok=False,
                    error=status_error_sentence(404))
    dumped = row.model_dump()
    assert (dumped.get("diagnosis") or {}).get("code") == "http_error_404", dumped
    assert dumped["diagnosis"]["retryable"] is False, dumped


def test_a_clean_crawl_row_pays_no_handle():
    """正控制：一行干干净净时整格缺席（不是 null）—— 「缺席即成功」不许在行级变。"""
    from dhole_mcp import wire as W
    from dhole_mcp.crawl import CrawlPage

    quiet = W._wire_dump(CrawlPage(url="https://origin.test/page", status=200,
                                  content_ok=True, content=["real words"]))
    assert "diagnosis" not in quiet, sorted(quiet)
    assert "error" not in quiet, sorted(quiet)


def test_a_crawl_row_asks_the_table_not_its_own_envelope():
    """归属锁：哨兵换掉 `row_diagnosis` ⇒ 行上的把手跟着变 —— 在构造点手写一颗 code 会红。"""
    from dhole_mcp.crawl import CrawlPage

    sentinel = {"code": "SENTINEL", "retryable": True, "recover_with": "from the sentinel"}
    original = Q.row_diagnosis
    Q.row_diagnosis = lambda error: dict(sentinel)
    try:
        row = CrawlPage(url="https://origin.test/x", status=500, content_ok=False,
                        error="http_error_500: server returned error status")
    finally:
        Q.row_diagnosis = original
    assert row.model_dump().get("diagnosis") == sentinel, row.model_dump()


def test_the_row_level_channels_that_own_an_error_all_own_a_handle():
    """前提锁：哪些行**有** error 格子，就必须有 diagnosis 格子 —— 名单由扫出来，不手抄。

    这把尺自己会随源码长：F114 给 `FeedRefusalModel` 装格子、F115 把名单改成扫，这一格
    是那条轴的下一环 —— 行级（`FeedResult` / `CrawlPage`）与信封级（`CrawlResponseModel`）
    现在都在册；将来有人新写一行带 `error` 却不认领把手，这里当场红。
    """
    import inspect
    from pydantic import BaseModel

    from dhole_mcp import crawl as CRAWL
    from dhole_mcp import feed as FEED
    from dhole_mcp import models as MODELS

    offenders = []
    for mod in (MODELS, FEED, CRAWL):
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if obj.__module__ != mod.__name__ or not issubclass(obj, BaseModel):
                continue
            if name == "RefusalHandle":
                continue
            fields = obj.model_fields
            if "error" in fields and "diagnosis" not in fields:
                offenders.append(f"{mod.__name__}.{name}")
    assert offenders == [], f"这些行有 error 却不认领把手：{offenders}"


# ─── F118 · 扇出的行住在正文的 JSON 里，也要认领同一副把手（R02-145 · 现场 r301）─────
#
# r301 打的是本机靶站的一发**普通扇出**（`/nav` 的 12 条导航链接，其中 `/cart` 没有路由）：
#   {"url": "…/cart",  "label": "购物车", "error": "http_error_404: server returned error status"}
#   {"url": "…/feed.xml", "label": "订阅", "标题": "", "error": "fields_no_match"}
# 两行都带着判决的原料，两行都没有把手。这一族与 F116 的 `CrawlPage` 同一条法，形状不同：**行不是
# 模型**，是 `payload["nested"]` 里的一堆裸 dict，随后被 `json.dumps` 塞进 `first.content`。所以
# 格子不会由 `RefusalHandle` 白送，得由这一族**唯一的装配位**来填（`nested.with_row_handles`）。
# 明写着不许做的事：在 `_one` 的五个写入点各手写一次 diagnosis —— 那是"靠每个构造点记得填"，
# 本仓一轮一轮修的就是它。

def test_a_nested_row_that_failed_wears_the_handle_its_error_names():
    from dhole_mcp import nested as NESTED
    from dhole_mcp.quality import status_error_sentence

    rows = NESTED.with_row_handles([
        {"url": "https://origin.test/cart", "label": "购物车",
         "error": status_error_sentence(404)},
        {"url": "https://origin.test/feed.xml", "label": "订阅", "标题": "",
         "error": "fields_no_match"}])
    assert rows[0]["diagnosis"]["code"] == "http_error_404", rows[0]
    assert rows[0]["diagnosis"]["retryable"] is False, rows[0]
    assert rows[1]["diagnosis"]["code"] == "fields_no_match", rows[1]
    assert rows[1]["diagnosis"]["recover_with"], (
        "这一行说的是「每个选择器都落空」。同一件事实的堂兄弟 `schema_no_match` 在册且给得出"
        "杠杆（server.py:7624 那句 check the selector），行级不许只交一个名字")


def test_a_clean_nested_row_pays_no_handle():
    """正控制：抽到了的那一行整格缺席 —— 「缺席即成功」在正文里的 JSON 上也成立。"""
    from dhole_mcp import nested as NESTED

    rows = NESTED.with_row_handles([{"url": "https://origin.test/work/1", "标题": "作品 1"}])
    assert "diagnosis" not in rows[0] and "error" not in rows[0], rows[0]


def test_a_nested_row_asks_the_table_not_its_own_assembler():
    """归属锁：哨兵换掉 `row_diagnosis` ⇒ 行上的把手跟着变（装配位自己算 code 会红）。"""
    from dhole_mcp import nested as NESTED

    sentinel = {"code": "SENTINEL", "retryable": True, "recover_with": "from the sentinel"}
    original = Q.row_diagnosis
    Q.row_diagnosis = lambda error: dict(sentinel)
    try:
        rows = NESTED.with_row_handles([{"url": "https://origin.test/x", "error": "whatever"}])
    finally:
        Q.row_diagnosis = original
    assert rows[0]["diagnosis"] == sentinel, rows[0]


def test_the_nested_rows_are_filled_at_one_owner():
    """前提锁：`payload["nested"]` 只许由那一位装配（五处行内写入各填一次会红）。"""
    import ast
    import inspect

    from dhole_mcp import server as SRV

    tree = ast.parse(inspect.getsource(SRV))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if not (isinstance(t, ast.Subscript)
                    and getattr(t.slice, "value", None) == "nested"
                    and getattr(t.value, "id", None) == "payload"):
                continue
            callee = getattr(node.value, "func", None)
            name = getattr(callee, "attr", None) or getattr(callee, "id", None)
            if name != "with_row_handles":
                bad.append(f"server.py:{node.lineno} 交的是 "
                           f"{name or ast.dump(node.value)[:40]}")
    assert not bad, f"这一族的行没有唯一的装配位：{bad}"
