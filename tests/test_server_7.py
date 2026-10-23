"""test_server_7.py —— 由 6 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pytest
from dhole_mcp import actions as A
from dhole_mcp import context as C
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.server import MasterFetchServer, ResponseModel
import pathlib
from dhole_mcp import charset as charset_mod
import json
from unittest.mock import AsyncMock
from dhole_mcp.server import _normalize_schema
from dhole_mcp import security
from dhole_mcp.server import _identity_value_error
import inspect
from dhole_mcp import browser as browser_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server


# ============================================================================
# 来源：test_a_long_task_says_where_it_is_while_it_runs.py
# ============================================================================
# 批次 12 · 长任务要一边跑一边说"走到哪了"（notifications/progress）。

SEED = "https://progress.example.com/"


class _RecordingSession:
    """只回答两件事：这次通知送出去了没有、送出去的是哪些数。"""

    def __init__(self, boom=False):
        self.events: list = []
        self.boom = boom

    async def report_progress(self, progress, total=None, message=None):
        if self.boom:
            raise RuntimeError("半断的连接：这条送不出去")
        self.events.append((progress, total, message))


@pytest.fixture()
def session():
    """把一颗假会话装上，用完摘下（ContextVar 会顺着同一 task 传下去）。"""
    def _install(rec):
        C.set_mcp_session(rec)
        return rec
    yield _install
    C.set_mcp_session(None)


class _FakeSite:
    def __init__(self, pages=None):
        self.fetched: list = []
        self.pages = pages or {}

    async def web_read(self, url, **kwargs):
        self.fetched.append(url)
        body = self.pages.get(url, "<p>leaf</p>")
        return ResponseModel(url=url, status=200, content=[body],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


def _page_with_links(links):
    return ("<html><body>"
            + "".join(f'<a href="{h}">t</a>' for h in links)
            + "</body></html>")


# ─── 1 · 那颗发送器自己的规矩 ────────────────────────────────────────────────

def test_no_session_sends_nothing_and_raises_nothing():
    """CLI / 直连 dispatch：会话是 None，进度这条通道必须静默。"""
    C.set_mcp_session(None)
    assert asyncio.run(C.report_progress(1.0, total=3.0, message="x")) is False


def test_a_send_failure_is_not_the_callers_problem(session):
    """客户端不收通知 ≠ 任务失败。这一条是整个改动的安全边界。"""
    rec = session(_RecordingSession(boom=True))
    assert asyncio.run(C.report_progress(1.0, total=3.0, message="x")) is False
    assert rec.events == []


def test_a_cancellation_is_not_swallowed(session):
    """取消是 BaseException，不能被"吞掉发送失败"顺手一起吃掉。"""
    class _Cancelled:
        async def report_progress(self, progress, total=None, message=None):
            raise asyncio.CancelledError()

    session(_Cancelled())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(C.report_progress(1.0, total=3.0, message="x"))


def test_the_recipient_sees_the_numbers_and_the_words(session):
    rec = session(_RecordingSession())
    asyncio.run(C.report_progress(2.0, total=5.0, message="two of five"))
    assert rec.events == [(2.0, 5.0, "two of five")]


# ─── 2 · 爬取：每一批一格，收尾说最终数字 ────────────────────────────────────

@pytest.mark.asyncio
async def test_a_crawl_reports_while_it_is_running(session):
    rec = session(_RecordingSession())
    links = [f"/p{i}" for i in range(6)]
    site = _FakeSite({SEED: _page_with_links(links)})
    out = await crawl_mod.web_crawl(site, SEED, max_pages=4, max_depth=1,
                                    concurrency=2, cache_ttl=0)
    assert out.pages_crawled >= 2, "爬取本身没跑起来，这条在测别的东西"
    assert rec.events, "几十秒的任务一条进度都没发 —— 那就是原来的行为"
    assert all(total == 4.0 for _, total, _ in rec.events), (
        "total 应当是调用方自己给的 max_pages，不是会涨会落的队列长度")
    seen = [p for p, _, _ in rec.events]
    assert seen == sorted(seen), f"进度倒退了：{seen}"


@pytest.mark.asyncio
async def test_the_last_crawl_report_is_the_final_count(session):
    """收尾那一格要说**最终**数字：只报"下一批开始前"会少说一整批。"""
    rec = session(_RecordingSession())
    links = [f"/p{i}" for i in range(6)]
    site = _FakeSite({SEED: _page_with_links(links)})
    out = await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                    concurrency=2, cache_ttl=0)
    assert rec.events[-1][0] == float(out.pages_crawled), (
        f"最后报的是 {rec.events[-1][0]}，实际爬了 {out.pages_crawled} 页")
    assert "crawl finished" in rec.events[-1][2]


@pytest.mark.asyncio
async def test_a_broken_progress_channel_does_not_stop_the_crawl(session):
    """反向守卫：客户端不收通知时，爬取照常返回它的页。"""
    rec = session(_RecordingSession(boom=True))
    links = [f"/p{i}" for i in range(4)]
    site = _FakeSite({SEED: _page_with_links(links)})
    out = await crawl_mod.web_crawl(site, SEED, max_pages=2, max_depth=1,
                                    concurrency=2, cache_ttl=0)
    assert out.pages_crawled == 2
    assert rec.events == []


# ─── 3 · 动作链：一步一步说 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_action_chain_reports_each_step(session):
    rec = session(_RecordingSession())
    page = object()          # wait / scroll 这类动作不碰 locator，假 page 就够
    action = A.build_page_action([{"wait": 1}, {"wait": 1}, {"wait": 1}])
    await action(page)   # page 只在需要 locator 的动作里才被碰；wait 不碰
    totals = [t for _, t, _ in rec.events]
    assert totals == [3.0, 3.0, 3.0], f"三步链的进度不该带别的 total：{totals}"
    assert [p for p, _, _ in rec.events] == [0.0, 1.0, 2.0], (
        "报的是「将要执行到第几步」，最后一步之后由响应本身收尾")
    assert "action 3/3" in rec.events[-1][2]


@pytest.mark.asyncio
async def test_a_single_action_chain_still_says_something(session):
    """一步的链也是长任务（那一步可能就是 20s 的 wait）：不能因为"只有一步"就不说。"""
    rec = session(_RecordingSession())
    action = A.build_page_action([{"wait": 5}])
    await action(object())
    assert len(rec.events) == 1 and rec.events[0][1] == 1.0


# ─── 4 · 续传计划的批边界 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_resumed_plan_reports_per_batch(session, monkeypatch):
    """`_crawl_plan_resume` 同样是几十秒的一趟，格点与 web_crawl 一致。"""
    rec = session(_RecordingSession())
    srv = MasterFetchServer()
    urls = [f"https://plan.example.com/p{i}" for i in range(6)]
    pid = crawl_mod.plan_store(urls)

    async def _fake_read(self, url, **kwargs):
        return ResponseModel(url=url, status=200, content=["body text"],
                             fetcher_used="http", content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "web_read", _fake_read)
    out = await srv._crawl_plan_resume(
        url="https://plan.example.com/", resume_token=f"{pid}:0",
        selector=None, schema=None, max_pages=4, max_content_chars_per=8000,
        max_total_chars=200000, concurrency=2, cache_ttl=0, force_fetcher=None,
        timeout=30000, deadline_ms=25000, ignore_robots=True)
    assert len(out.pages) >= 2, "续传本身没跑起来，这条在测别的东西"
    assert rec.events, "一份计划的续传几十秒，期间一个字都没发"
    assert all(t == 4.0 for _, t, _ in rec.events), (
        "total 该是这次调用被允诺的上限，不是计划里 URL 的条数")
    seen = [p for p, _, _ in rec.events]
    assert seen == sorted(seen), f"进度倒退了：{seen}"


# ============================================================================
# 来源：test_a_named_charset_that_decodes_cleanly_still_names_the_rival.py
# ============================================================================
# 点名的字符集**严格解得开**这串字节，回执也不许把这份读数写成判定 —— 字节还允许另一份。

KO_TEXT = "한글 문서를 여는 방법: 인코딩과 변환 순서"
ZH_GBK = "钢轨磨耗在线检测：波形磨耗与磨耗面积计算，见表 4（附录）。"
ZH_BIG5 = "鋼軌磨耗在線檢測：波形磨耗與磨耗面積計算，見表四（附錄）。"


def _write(tmp_path: pathlib.Path, name: str, body: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


async def _parse(tmp_path, name, body, **kw):
    path = _write(tmp_path, name, body)
    return await MasterFetchServer().doc_parse(file_path=path, **kw)


# ─── 1 · 那位作者：相容不等于测量 ───


class TestTheRuler:

    def test_a_clean_rival_reading_contests_the_named_one(self):
        """现场那一格：GBK 字节点名 big5，两份读数都干净而文本不同。"""
        body = ZH_GBK.encode("gbk")
        body.decode("big5")                      # 前提：点名的这颗确实解得开
        assert charset_mod.encoding_basis(body, "big5", "big5") == "contested"

    def test_identical_readings_stay_a_verdict(self):
        """GBK 件点名 gbk：自动那颗 gb18030 给出**同一份文本** —— 没什么好交代的。"""
        body = ZH_GBK.encode("gbk")
        assert charset_mod.decode_file_bytes(body, "")[1] == "gb18030"
        assert charset_mod.decode_file_bytes(body, "gbk")[0] \
            == charset_mod.decode_file_bytes(body, "")[0]
        assert charset_mod.encoding_basis(body, "gbk", "gbk") == "declared"

    def test_a_rival_that_is_itself_in_doubt_does_not_contest(self):
        """真 big5 件：gb18030 那份读成一堆 PUA（damage 14、在疑），它没有资格竞争。"""
        body = ZH_BIG5.encode("big5")
        rival, rival_used, rival_damage = charset_mod.decode_file_bytes(body, "")
        assert (rival_used, rival_damage) == ("gb18030", 14), (
            f"这一格的前提是竞争者自身可疑：{rival_used}/{rival_damage}")
        assert charset_mod.charset_is_in_doubt(rival)
        assert charset_mod.encoding_basis(body, "big5", "big5") == "declared"

    def test_utf8_and_ascii_names_stay_quiet(self):
        body = ZH_GBK.encode("utf-8")
        assert charset_mod.encoding_basis(body, "utf-8", "utf-8") == "declared"
        plain = b"plain ascii text only"
        assert charset_mod.encoding_basis(plain, "cp1252", "cp1252") == "declared"

    def test_the_fallback_branch_is_untouched(self):
        """点名的解不开、由检测顶上那一支（R02-021）一字不改。"""
        body = KO_TEXT.encode("euc-kr")
        assert charset_mod.encoding_basis(body, "shift_jis", "gb18030") == "fallback"
        assert charset_mod.encoding_basis(body, "", "gb18030") == "guessed"


# ─── 2 · 端到端：两个名字都要在场，正文不许换 ───


class TestTheReceipt:

    @pytest.mark.asyncio
    async def test_the_named_reading_is_still_the_one_that_ships(self, tmp_path):
        """反面锁里最硬的一条：这一刀不搬正文，只说话。"""
        r = await _parse(tmp_path, "gbk-named-big5.txt", ZH_GBK.encode("gbk"),
                         encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding") == md.get("encoding_asked") == "big5", md
        body_text = "\n".join(r.content)
        assert ZH_BIG5[:6] not in body_text          # 没有偷偷改按 gb 读
        assert "詩" in body_text, (
            f"点名的那颗没在跑：{body_text[:60]}")

    @pytest.mark.asyncio
    async def test_the_receipt_names_both_charsets(self, tmp_path):
        r = await _parse(tmp_path, "gbk-named-big5.txt", ZH_GBK.encode("gbk"),
                         encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding_basis") == "contested", (
            f"相容被写成了判定：{md}")
        na = r.next_action or ""
        assert "big5" in na and "gb18030" in na, (
            f"那句话没有点名两颗名字：{na!r}")
        assert "guess" not in na.lower(), (
            f"按吩咐读的那一发被叫成猜：{na!r}")

    @pytest.mark.asyncio
    async def test_this_is_not_reported_as_a_failure(self, tmp_path):
        r = await _parse(tmp_path, "gbk-named-big5.txt", ZH_GBK.encode("gbk"),
                         encoding="big5")
        assert not r.error, f"一次按吩咐的读取被报成失败：{r.error!r}"
        assert r.content_ok is True, "见上一条的取舍：不换一个新谎进来"

    @pytest.mark.asyncio
    async def test_a_correct_name_with_a_clean_rival_says_the_same_kind_of_thing(
            self, tmp_path):
        """韩文件点名 euc-kr（点**对**了）：竞争者 gb18030 也干净，句子照说。

        这一格不是误报而是同一件事：dhole 确实说不出哪份对。它守的是 F3 那批反面锁的
        职责（正文是原语言、不叫"猜"、content_ok 不动），改的只有 basis 那一颗。
        """
        r = await _parse(tmp_path, "korean-right.txt", KO_TEXT.encode("euc-kr"),
                         encoding="euc-kr")
        md = r.metadata or {}
        assert md.get("encoding_basis") == "contested", md
        assert KO_TEXT in "\n".join(r.content), "解药那一发正文得是原语言"
        assert "guess" not in (r.next_action or "").lower(), r.next_action

    @pytest.mark.asyncio
    async def test_a_name_agreeing_with_the_rival_still_says_nothing(self, tmp_path):
        """两份读数同一份文本时，句子必须不出现（不许把这格做成噪声）。"""
        r = await _parse(tmp_path, "gbk-named-gbk.txt", ZH_GBK.encode("gbk"),
                         encoding="gbk")
        md = r.metadata or {}
        assert "encoding_basis" not in md, f"两份读数逐字相同却仍开口：{md}"
        assert "contested" not in (r.next_action or "")


# ─── 3 · 一位作者与手册 ───


def test_the_new_value_has_one_author():
    import inspect

    src = inspect.getsource(charset_mod)
    assert src.count('"contested"') == 1, "判据只有 `encoding_basis` 一位作者"


def test_the_manual_carries_the_encoding_basis_vocabulary():
    """回执要发第四颗值，手册就得把这一族的四颗值一次说清（它今天一个字也没有）。"""
    import inspect
    from pathlib import Path

    from dhole_mcp import quality as quality_mod
    doc = (Path(inspect.getsourcefile(quality_mod)).resolve().parent
           / "skills" / "dhole-web" / "references" / "fields.md"
           ).read_text(encoding="utf-8")
    for value in ("declared", "fallback", "guessed", "contested"):
        assert f"`{value}`" in doc, f"手册里没有 encoding_basis 的这颗值：{value}"
    assert "encoding_basis" in doc, "手册里没有这颗字段"


# ============================================================================
# 来源：test_schema_param.py
# ============================================================================
# web_read 的 ``schema`` 参数：要么真的产出结构化 JSON，要么明确报错。

HTML = """<html><head><title>T</title></head><body>
<h1 class="headline">Structured Title</h1>
<span class="price">$29.99</span>
<ul class="feature-list"><li>Alpha</li><li>Beta</li></ul>
</body></html>"""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "selector": "h1.headline"},
        "price": {"type": "string", "selector": ".price"},
        "features": {"type": "array", "selector": ".feature-list li"},
    },
}


def _html_result(extracted_type: str = "html") -> ResponseModel:
    return ResponseModel(
        status=200, content=[HTML], url="https://example.com",
        fetcher_used="http", content_type="text/html",
        extracted_type=extracted_type,
    )


class TestNormalizeSchema:

    def test_none_means_no_schema(self):
        assert _normalize_schema(None) is None

    def test_blank_string_and_empty_dict_mean_no_schema(self):
        assert _normalize_schema("   ") is None
        assert _normalize_schema({}) is None

    def test_object_schema_passes_through(self):
        assert _normalize_schema(SCHEMA) is SCHEMA

    def test_json_string_schema_is_parsed(self):
        assert _normalize_schema(json.dumps(SCHEMA)) == SCHEMA

    def test_auto_type_and_mode_are_accepted(self):
        assert _normalize_schema({"type": "auto"}) == {"type": "auto"}
        assert _normalize_schema({"mode": "auto"}) == {"mode": "auto"}

    def test_schema_without_properties_raises(self):
        with pytest.raises(ValueError, match="nothing to extract"):
            _normalize_schema({"type": "object"})

    def test_empty_properties_raises(self):
        with pytest.raises(ValueError, match="nothing to extract"):
            _normalize_schema({"type": "object", "properties": {}})

    def test_malformed_json_string_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _normalize_schema("{not json")

    def test_non_object_schema_raises(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            _normalize_schema(["h1"])


class TestContainerIntentAtTheTopLevelIsRefused:
    """批次12 回归：顶层 `{"type": "array", "selector": ".athing", ...}` 此前被
    宽容放行，而抽取端只读 `properties` —— 顶层 selector 被无视，HN 首页 30 条
    实测只回 1 条：看起来成功，结果是错的。现在发请求前具名拒绝。"""

    WRONG = {"type": "array", "selector": ".athing",
             "properties": {"title": {"selector": ".titleline a"}}}

    def test_top_level_container_shape_is_refused(self):
        with pytest.raises(ValueError, match="container"):
            _normalize_schema(dict(self.WRONG))

    def test_selector_with_properties_without_type_array_is_refused(self):
        with pytest.raises(ValueError, match="container"):
            _normalize_schema({"selector": ".quote",
                               "properties": {"text": {"selector": ".text"}}})

    def test_type_array_with_bare_selector_is_refused(self):
        with pytest.raises(ValueError, match="container"):
            _normalize_schema({"type": "array", "selector": ".quote"})

    def test_refusal_carries_the_correct_shape(self):
        with pytest.raises(ValueError) as exc:
            _normalize_schema(dict(self.WRONG))
        assert '"properties": {"stories"' in str(exc.value)

    def test_page_wide_scalar_properties_still_pass(self):
        ok = {"properties": {"title": {"selector": "h1"}}}
        assert _normalize_schema(ok) is ok
        assert _normalize_schema({"type": "auto"}) == {"type": "auto"}

    @pytest.mark.asyncio
    async def test_container_intent_schema_is_refused_before_any_fetch(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())
        out = await server.web_read("https://example.com",
                                    schema=dict(self.WRONG), cache_ttl=0)
        assert out.status == 0 and out.content == []
        assert "container" in out.error
        server._auto_escalate.assert_not_awaited()


class TestSchemaReachesStructuredExtraction:
    """契约：传了 schema 就必须拿到 JSON，不能悄悄给 markdown。"""

    def _server(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())
        return server

    @pytest.mark.asyncio
    async def test_object_schema_returns_structured_json(self):
        server = self._server()
        out = await server.web_read("https://example.com", schema=SCHEMA, cache_ttl=0)

        assert out.extracted_type == "structured"
        payload = json.loads(out.content[0])
        assert payload["title"] == "Structured Title"
        assert payload["features"] == ["Alpha", "Beta"]

    @pytest.mark.asyncio
    async def test_json_string_schema_is_not_silently_dropped(self):
        """回归：字符串形式的 schema 此前会静默退回 markdown。"""
        server = self._server()
        out = await server.web_read(
            "https://example.com", schema=json.dumps(SCHEMA), cache_ttl=0,
        )

        assert out.extracted_type == "structured"
        assert json.loads(out.content[0])["price"] == "$29.99"

    @pytest.mark.asyncio
    async def test_unusable_schema_is_rejected_before_any_fetch(self):
        """回归：无 properties 的 schema 此前会静默退回 markdown。

        拒绝的形式也进了契约：抛异常会被兜底成 is_error 的 MCP 结果，与 web_resolve
        之类返回结构化错误的做法不一致（BUG-16）。现在是 status=0 + error，且仍然
        不发请求、不返回 markdown。
        """
        server = self._server()
        out = await server.web_read(
            "https://example.com", schema={"type": "object"}, cache_ttl=0,
        )
        assert out.status == 0
        assert out.content == []
        assert "nothing to extract" in out.error
        server._auto_escalate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_schema_still_returns_markdown(self):
        """不传 schema 是正常用法，不得被顺手改成结构化路径。"""
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result("markdown"))
        server._finalize_result = AsyncMock(side_effect=lambda result, *args: result)

        out = await server.web_read("https://example.com", cache_ttl=0)

        assert out.extracted_type != "structured"
        assert out.content == [HTML]


class TestSchemaReachesTheTool:
    """``schema`` 只有一个通道：顶层参数。它必须真的抵达 web_read。"""

    @pytest.mark.asyncio
    async def test_schema_drives_structured_extraction(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())

        out = await server._dispatch(
            "web_read",
            {"url": "https://example.com", "schema": SCHEMA, "cache_ttl": 0},
        )
        assert out[1]["extracted_type"] == "structured"


# ============================================================================
# 来源：test_a_value_that_cannot_apply_is_refused_where_it_is_stored.py
# ============================================================================
# 存进去就用不上的值，要在存的那一侧就响（评审 R02-104 · R02-105）。

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


# ============================================================================
# 来源：test_render_settle_b5.py
# ============================================================================
# B5-13d：渲染时机不再是一颗参数（§6 表的最后两行 / 三条新规矩第 1 条）。

URL = "https://example.com/"


class _Page:
    """playwright page 的最小替身：只记 `_wait_for_stability` 会碰的两件事。"""

    def __init__(self, *, reading=None, evaluate_raises=False, idle_raises=False):
        self.states: list[tuple[str, object]] = []
        self._reading = reading if reading is not None else {"text": 5000, "scripts": 3}
        self._evaluate_raises = evaluate_raises
        self._idle_raises = idle_raises

    async def wait_for_load_state(self, state, timeout=None):
        self.states.append((state, timeout))
        if state == "networkidle" and self._idle_raises:
            raise TimeoutError("timeout 5000ms exceeded")

    async def evaluate(self, script):
        if self._evaluate_raises:
            raise RuntimeError("execution context destroyed")
        return self._reading


def _settle(page) -> bool:
    """跑真的 `_wait_for_stability`，返回它有没有付那 5 秒。"""
    session = object.__new__(browser_mod.BrowserSession)  # 造一台浏览器不是本题
    return asyncio.run(session._wait_for_stability(page))


class TestTheVerdictIsAPureFunction:
    """读数 → 结论，一张表说清什么叫"这页还没长出来"。"""

    def test_short_text_with_a_script_bundle_is_a_shell(self):
        assert browser_mod._looks_unrendered({"text": 40, "scripts": 2}) is True

    def test_a_page_with_real_text_has_rendered(self):
        assert browser_mod._looks_unrendered({"text": 4000, "scripts": 9}) is False

    def test_short_text_without_scripts_is_a_small_page_not_a_shell(self):
        """nginx 的 404 也是几十字符的正文：没有脚本就没法说"脚本还没跑"。"""
        assert browser_mod._looks_unrendered({"text": 30, "scripts": 0}) is False

    def test_the_threshold_is_the_same_number_as_the_extraction_side(self):
        # server 的壳页判定拿"抽取后的文本 < _JS_SHELL_MIN_TEXT_CHARS"做门槛；
        # 浏览器这边认的是"可见文本还没长到那个数"。两个数一错位，就会出现一条轴
        # 认得出、另一条轴白等 5 秒（或该等的不等）的裂缝。
        assert browser_mod._UNRENDERED_MAX_TEXT_CHARS == server_mod._JS_SHELL_MIN_TEXT_CHARS

    @pytest.mark.parametrize("reading", [None, {}, "junk", {"text": "abc", "scripts": 1},
                                         {"text": -1, "scripts": 3}])
    def test_an_unreadable_dom_is_not_a_shell(self, reading):
        assert browser_mod._looks_unrendered(reading) is False


class TestTheSettleIsConditional:

    def test_a_page_that_has_not_rendered_pays_the_idle_wait(self):
        page = _Page(reading={"text": 20, "scripts": 4})
        assert _settle(page) is True
        assert ("load", None) in page.states
        assert ("networkidle", 5000) in page.states, \
            "认出壳页却不补 networkidle —— 这颗参数删了就等于把能力删了"

    def test_a_rendered_page_pays_nothing(self):
        page = _Page(reading={"text": 8000, "scripts": 4})
        assert _settle(page) is False
        assert not [s for s in page.states if s[0] == "networkidle"], \
            "内容早就在了还要静等 5 秒，是把读者的时间当自己的"

    def test_an_unreadable_dom_does_not_cost_5_seconds(self):
        page = _Page(evaluate_raises=True)
        assert _settle(page) is False
        assert not [s for s in page.states if s[0] == "networkidle"]

    def test_the_idle_wait_is_capped_and_a_timeout_is_silent(self):
        page = _Page(reading={"text": 20, "scripts": 4}, idle_raises=True)
        assert _settle(page) is True
        assert page.states.count(("networkidle", 5000)) == 1, \
            "5 秒上限是这颗的全部意义：认出了壳也不能把调用拖成无限等"

    def test_a_load_that_never_fires_still_reaches_the_verdict(self):
        """wait_for_load_state 抛（超时 / 导航已定）不是判定失败的理由。"""
        class _Ugly(_Page):
            async def wait_for_load_state(self, state, timeout=None):
                self.states.append((state, timeout))
                raise RuntimeError("target closed")

        page = _Ugly(reading={"text": 15, "scripts": 1})
        assert _settle(page) is True


class TestTheCallSurfaceLostBothKnobs:

    def _props(self, tool):
        defs = {d["name"]: d for d in Server._TOOL_DEFS}
        return defs[tool]["inputSchema"]["properties"]

    def test_neither_tool_advertises_a_timing_knob(self):
        # A1 后独立 screenshot 工具不在册；web_act 的 props 一起查。
        for tool in ("web_read", "web_act"):
            props = self._props(tool)
            for knob in ("wait", "network_idle"):
                assert knob not in props, f"{tool} 还在教 {knob}"

    def test_web_read_advertises_thirty_one_names(self):
        """35 → 30（13c 身份）→ 28（13d 时机）→ 25（14a 写操作）→ 24（14b 动作链）→ 27（14d nested_*）→ 28（W1 headers）→ 31（R3/R4）。

        §3-12 的 ≤20 已由作者裁决**放宽成棘轮 27**（"能力优先"）：差的那几颗是
        §3-16 那道门与读法本身，守卫在 tests/test_wire_shape.py。

        24 → 27 是 B5-17 的全量跑补上的：E-13 那批加进 `nested_selector` /
        `nested_fields` / `nested_max` 三颗时没回改这条普查，于是它连着几个提交都在
        报一个不存在的数字。普查写死是有意义的（少一颗就是能力从门上消失），但它的
        意义全在于**跟着改** —— 一条常年红着没人理的普查比没有普查更糟，因为它教会
        大家红是可以接受的。

        27 → 28 是体验修复刀3-W1 的 `headers`：一次零点名的抓取实测发 15 颗头而参数面
        上没有一颗能改它（旧拼写 `extra_headers` 是 §3-17 具名拒绝的身份通道）。同一刀
        把 `stealthy_headers` 那两颗字面量交给档位表读，公开面因此只多这一颗。

        28 → 31（2026-10-23）：`include_headers` / `save_to` / `max_save_bytes`。这三颗
        不是三个新旋钮，是**这扇门的另外两种产出**——`web_read` 从前只会交出"抽出来的
        正文"，现在"这一发的证据"（状态行 / 响应头 / 逐跳 302 链）与"这一发的字节"
        （原样落盘）也接上了同一条链路。挤不挤得进来的账写在 tests/test_wire_shape.py
        的 PARAM_CAP 上：那里是决定，这里只是让"还剩几颗"跟着动。
        """
        assert len(server_mod._TOP_LEVEL_ARGS["web_read"]) == 31, \
            sorted(server_mod._TOP_LEVEL_ARGS["web_read"])

    def test_web_act_carries_no_timing_knob_either(self):
        assert not {"wait", "network_idle"} & server_mod._TOP_LEVEL_ARGS["web_act"]

    @pytest.mark.parametrize("knob", ["wait", "network_idle"])
    def test_passing_a_retired_timing_knob_is_refused_not_swallowed(self, knob):
        with pytest.raises(ValueError, match="Unsupported argument") as e:
            asyncio.run(Server()._dispatch("web_read", {"url": URL, knob: 5}))
        assert knob in str(e.value)


class TestTheInternalChainHasNoSourceLeft:
    """形参只有调用方能填，而调用方没了 —— 留着就是死码冒充能力。"""

    def test_the_browser_takes_no_timing_knobs(self):
        for fn in (browser_mod.BrowserSession.__init__,
                   browser_mod.BrowserSession.fetch):
            params = inspect.signature(fn).parameters
            for knob in ("wait", "network_idle"):
                assert knob not in params, f"{fn.__qualname__} 还收 {knob}"

    def test_the_server_tiers_take_no_timing_knobs(self):
        for name in ("get", "stealthy_fetch", "bulk_stealthy_fetch",
                     "_force_fetch", "_auto_escalate", "_smart_fetch_bulk",
                     "web_read", "web_act"):
            params = inspect.signature(getattr(Server, name)).parameters
            for knob in ("wait", "network_idle"):
                assert knob not in params, f"{name} 还收 {knob}"


# ============================================================================
# 来源：test_a_named_encoding_that_did_not_run_says_which_one_did.py
# ============================================================================
# 点名了一颗 `encoding=`，跑的却是另一颗 —— 回执必须说清「你要的」与「用的」是两个。
# 重名消歧：KO_TEXT → KO_TEXT__a_named_encoding_that_di_a13e、_parse → _parse__a_named_encoding_that_di_a13e、_write → _write__a_named_encoding_that_di_a13e

KO_TEXT__a_named_encoding_that_di_a13e = "한글 문서를 여는 방법: 인코딩과 변환 순서"
ZH_TEXT = "第 3 段讲的是同一个专题的进展，句式与用词都逐段不同。"


def _write__a_named_encoding_that_di_a13e(tmp_path: pathlib.Path, name: str, body: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


async def _parse__a_named_encoding_that_di_a13e(tmp_path, name, body, **kw):
    path = _write__a_named_encoding_that_di_a13e(tmp_path, name, body)
    return await MasterFetchServer().doc_parse(file_path=path, **kw)


class TestTheNamedCharsetAndTheOneThatRan:

    @pytest.mark.asyncio
    async def test_a_name_that_could_not_decode_is_reported_as_a_fallback(
            self, tmp_path):
        r = await _parse__a_named_encoding_that_di_a13e(tmp_path, "korean-euckr.txt", KO_TEXT__a_named_encoding_that_di_a13e.encode("euc-kr"),
                         encoding="shift_jis")
        md = r.metadata or {}
        assert md.get("encoding_asked") == "shift_jis", (
            f"回执里没有「你点的那一颗」：{md}")
        assert md.get("encoding") != "shift_jis", (
            f"点名的那颗其实没在跑，却仍按它交差：{md}")
        assert md.get("encoding_basis") == "fallback", (
            f"回落被当成了声明：{md}")
        assert "shift_jis" in (r.next_action or "") and (md.get("encoding") or "") in (
            r.next_action or ""), (
            f"那句话没有点名两颗名字：{r.next_action!r}")

    @pytest.mark.asyncio
    async def test_a_second_shape_with_the_same_fallback_big5_on_gbk(self, tmp_path):
        """P3 的第二例：GBK 件点名 big5，而那串字节**解不开** big5。

        （写这一格时先撞了一次反例：`ZH_TEXT.encode('gbk')` 那串字节是解得开 big5 的，
        于是它是另一件事 —— 见下面那一发 `names_a_charset_that_did_decode`。同名而解得开
        = 调用方领走这次读数；点名而解不开 = 检测顶上，必须申报。）
        """
        body = ("这台设备的电气参数见表 4（附录 B），另有三处标注为「待确认」。"
                .encode("gbk"))
        with pytest.raises(UnicodeDecodeError):
            body.decode("big5")   # 前提：这颗名字在这串字节上真的解不开
        r = await _parse__a_named_encoding_that_di_a13e(tmp_path, "gbk-big5.txt", body, encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding_asked") == "big5"
        assert md.get("encoding_basis") == "fallback", f"这一发同样静默回落：{md}"
        assert "big5" in (r.next_action or ""), r.next_action

    @pytest.mark.asyncio
    async def test_names_a_charset_that_did_decode_is_the_callers_own_reading(self,
                                                                              tmp_path):
        """反面锁：点了名而且它解得开 —— 正文就是那颗读出来的，一个字都不许换。

        R02-F8 之后这一格多了一句"这批字节还允许第二种干净的读法"（`encoding_basis=
        contested`），但**不**换正文、**不**翻判词：把一次按吩咐的读取报成失败，换来的
        只是调用方从此不信这些字段（R01 起写着的取舍，本刀继续守）。这一格守的就是这条。
        """
        body = ZH_TEXT.encode("gbk")
        body.decode("big5")   # 前提：这颗名字解得开（与上一发只有这一点不同）
        r = await _parse__a_named_encoding_that_di_a13e(tmp_path, "gbk-named-big5.txt", body, encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding") == md.get("encoding_asked") == "big5", md
        assert body.decode("big5")[:6] in "\n".join(r.content), (
            f"点名的那颗被换掉了：{r.content}")
        assert ZH_TEXT not in "\n".join(r.content), (
            "没点名的那颗反而跑了 —— 那是静默替换，不是交代")
        assert md.get("encoding_basis") == "contested", (
            f"相容被写成了判定，一句交代都没有：{md}")

    @pytest.mark.asyncio
    async def test_a_name_that_decoded_is_a_verdict_and_says_no_more(self, tmp_path):
        """反面锁：点中了就不许多一句「这是猜的」（R01 那批契约的原样）。"""
        r = await _parse__a_named_encoding_that_di_a13e(tmp_path, "korean-right.txt", KO_TEXT__a_named_encoding_that_di_a13e.encode("euc-kr"),
                         encoding="euc-kr")
        md = r.metadata or {}
        assert md.get("encoding") == "euc-kr", md
        assert md.get("encoding_asked") == "euc-kr", md
        assert md.get("encoding_basis") != "guessed", (
            f"跑的就是吩咐的那一颗，却被叫成猜：{md}")
        assert "guess" not in (r.next_action or "").lower(), r.next_action
        assert KO_TEXT__a_named_encoding_that_di_a13e in "\n".join(r.content), "解药那一发正文得是原语言"

    @pytest.mark.asyncio
    async def test_no_declaration_keeps_the_old_guess_wording(self, tmp_path):
        """没人点名时那一支一字不动：gb18030 干净解开 → basis=guessed + 原句 hedge。"""
        r = await _parse__a_named_encoding_that_di_a13e(tmp_path, "sjis-nobody-named.txt", KO_TEXT__a_named_encoding_that_di_a13e.encode("euc-kr"))
        md = r.metadata or {}
        assert md.get("encoding_basis") == "guessed", md
        assert "encoding_asked" not in md, "没人点名，就不要凭空写一颗被点名的"

    @pytest.mark.asyncio
    async def test_the_fallback_never_doubles_as_a_failure(self, tmp_path):
        """这一发没有「失败」可言：正文照样交回，error 不写，content_ok 不翻假。"""
        r = await _parse__a_named_encoding_that_di_a13e(tmp_path, "fallback-not-an-error.txt", KO_TEXT__a_named_encoding_that_di_a13e.encode("euc-kr"),
                         encoding="shift_jis")
        assert not r.error, f"回落被报成了错误：{r.error!r}"
        assert r.content_ok is True, (
            "把一次替换读数报成失败，换来的只是调用方从此不信 content_ok")
        assert "\n".join(r.content), "正文还是要给的 —— 那正是他要拿去看的字"

    def test_the_ruler_has_one_author_and_a_total_truth_table(self):
        """`encoding_basis` 是这一族问题唯一的作者（返回值：declared/contested/fallback/guessed/''）。

        第五颗值是 R02-037 加的那一格：点名的解得开、而这批字节另有第二种干净读法。
        `declared` 仍然有它的例子 —— 两种读法给出**同一份文本**时（GBK 件点名 gbk，自动
        那颗走 gb18030 而文本逐字相同），没什么好交代的。
        """
        body = KO_TEXT__a_named_encoding_that_di_a13e.encode("euc-kr")
        assert charset_mod.encoding_basis(body, "euc-kr", "euc-kr") == "contested"
        assert charset_mod.encoding_basis(body, "shift_jis",
                                          "gb18030") == "fallback"
        assert charset_mod.encoding_basis(body, "", "gb18030") == "guessed"
        assert charset_mod.encoding_basis(
            KO_TEXT__a_named_encoding_that_di_a13e.encode("utf-8"), "", "utf-8") == ""
        assert charset_mod.encoding_basis(
            KO_TEXT__a_named_encoding_that_di_a13e.encode("utf-16"), "", "utf-16") == ""
        gbk_body = ZH_TEXT.encode("gbk")
        assert charset_mod.encoding_basis(gbk_body, "gbk", "gbk") == "declared"
        assert charset_mod.encoding_basis(
            gbk_body, "big5", "big5") == "contested"

    def test_the_two_names_are_the_same_name_when_they_are_the_same_charset(self):
        """名字要归一化再比：`GBK` 与 `gbk`、`utf-8` 与 `utf_8` 不是两颗字符集。"""
        body = ZH_TEXT.encode("gbk")
        for asked in ("gbk", "GBK", " gbk "):
            assert charset_mod.encoding_basis(body, asked, "gbk") == "declared", asked
