"""17.0 §5 E-13：`nested_*` 三颗把「列表页 → 每项详情页」摊成一次调用。

判据不是「能不能抽出两层」，而是**这一颗参数有没有把责任从调用方手上偷走**。
扇出以前是 agent 自己做的事：读首页、拿到 8 个 URL、再发 8 次 web_read —— 那 8 次
每次都各自过 robots.txt、各自有回执。现在一次调用替它做完，robots 检查就必须跟着
搬进来（测试里那条 `robots_skipped` 是这一刀的存在理由，不是装饰）。

另一半是**"少给了"必须说出口**：`nested_max` 封顶、第二层页面全空、选择器一个都没
命中，这三种情况过去都能以"一个看着成功的 JSON"交付。实测报告 §5 E-13 的原始诉求
是"8×N 一次拿到"，而一次拿到 8 条、实际有 12 个专题，是这条参数唯一能造成的错。

桩全部绑在 `_auto_escalate` 的**真实签名**上（G14 那一族的教训：吞 `**kwargs` 的桩
让参数错位测不出来）。"零请求"那几条读的就是这份名册，所以它们同时也是错位钉子的
证人。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import nested as nested_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server
from dhole_mcp.server import ResponseModel

LIST_URL = "https://list.test/"
FIELDS = [{"name": "作品", "selector": ".work-item a", "type": "array"}]

# 三种"链接藏在不同位置"的形状，一次覆盖 `_href_for` 的三条找法：
# 卡片自己不是 <a>（href 在肚子里）、卡片本身就是 <a>、有 class 但没 href。
LIST_HTML = """<html><body>
<div class="topic-card"><a href="/t/1">专题一</a><span class="n">20 件</span></div>
<a class="topic-card" href="/t/2">专题二</a>
<div class="topic-card"><span>专题三</span></div>
<div class="topic-card"><a href="/t/1">专题一（重复）</a></div>
<div class="topic-card"><a href="javascript:void(0)">按钮</a></div>
</body></html>"""

DETAIL = {
    "https://list.test/t/1": '<html><body><div class="work-item"><a>作品A</a></div>'
                             '<div class="work-item"><a>作品B</a></div></body></html>',
    "https://list.test/t/2": '<html><body><div class="work-item"><a>作品C</a></div>'
                             '</body></html>',
}


class _Verdict:
    def __init__(self, allowed: bool):
        self.allowed = allowed
        self.reason = "disallowed" if not allowed else "allowed"


def _stub_pages(monkeypatch, pages: dict, *, calls: list, max_live=None,
                robots=lambda url: True):
    """把取回层换成一张 URL→HTML 的名册，并记录每一次到达。"""
    import inspect

    sig = inspect.signature(Server._auto_escalate)
    live = {"n": 0, "peak": 0}
    limit = max_live or 99

    async def fake(self, *args, **kwargs):
        bound = sig.bind(self, *args, **kwargs).arguments
        url = bound["url"]
        if not robots(url):
            # robots 是在 `_auto_escalate` 之前拦的；走到这里还回 False 说明那条
            # 链接被发出去了 —— 断言由调用方的 `calls` 完成，这里只留痕。
            calls.append(("robots-leaked", url))
        if live["n"] >= limit:
            raise AssertionError("并发超过了 NESTED_CONCURRENCY")
        live["n"] += 1
        live["peak"] = max(live["peak"], live["n"])
        try:
            calls.append((url, dict(bound)))
            await asyncio.sleep(0.01)
            html = pages.get(url)
            if html is None:
                return ResponseModel(url=url, status=404, content=[],
                                     fetcher_used="http", error="not found")
            return ResponseModel(url=url, status=200, content=[html],
                                 fetcher_used="http", extracted_type="html",
                                 content_type="text/html")
        finally:
            live["n"] -= 1

    monkeypatch.setattr(Server, "_auto_escalate", fake)
    return live


def _stub_robots(monkeypatch, disallowed=()):
    async def fake_check(url, **kw):
        return _Verdict(url not in disallowed)

    monkeypatch.setattr(server_mod, "check_robots", fake_check)


def _run(**kw):
    return asyncio.run(Server().web_read(**kw))


def _payload(out):
    assert out.content, out.error
    return json.loads(out.content[0])


class TestOneCallTwoLayers:
    """验收标准本体：一句 nested_selector 拿到 8×N，一次调用。"""

    def test_the_list_page_and_its_detail_pages_come_back_grouped(self, monkeypatch):
        calls: list = []
        pages = {LIST_URL: LIST_HTML, **DETAIL}
        _stub_pages(monkeypatch, pages, calls=calls)
        _stub_robots(monkeypatch)

        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=FIELDS, cache_ttl=0)

        assert out.error == "", out.error
        assert out.extracted_type == "structured"
        records = _payload(out)["nested"]
        # 一个容器一条记录：4 张卡片里有 href 的是 2 个去重后的 URL。
        assert [r["url"] for r in records] == ["https://list.test/t/1",
                                               "https://list.test/t/2"]
        assert [r["作品"] for r in records] == [["作品A", "作品B"], ["作品C"]], \
            "第二层的列表被拍平或串了页 —— 分组没了就等于这次扇出白做"
        assert [r["label"] for r in records] == ["专题一", "专题二"]

    def test_the_labels_come_from_the_card_not_the_detail_page(self, monkeypatch):
        """label 是「哪一项」的唯一身份证：出错时它决定调用方能不能认出抓错了。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=FIELDS, cache_ttl=0)
        assert _payload(out)["nested"][1]["label"] == "专题二"

    def test_a_href_on_an_ancestor_resolves(self, monkeypatch):
        """命中元素自己不是 <a>、肚子里也没有，链接在它外面最近的 <a> 上。"""
        calls: list = []
        html = ('<html><body><a href="/t/1"><span class="card">专题一</span></a>'
                '</body></html>')
        _stub_pages(monkeypatch, {LIST_URL: html, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".card", nested_fields=FIELDS,
                   cache_ttl=0)
        assert _payload(out)["nested"][0]["url"] == "https://list.test/t/1"

    def test_every_second_layer_page_is_read_as_a_whole_document(self, monkeypatch):
        """参数错位钉子（G14 同一族）：抽取器要看到整篇文档，不是调用方的返回上限。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        _run(url=LIST_URL, nested_selector=".topic-card", nested_fields=FIELDS,
             max_content_chars=2000, cache_ttl=0)
        for url, kwargs in calls:
            assert kwargs["extraction_type"] == "html", url
            assert kwargs["max_chars"] == server_mod._SCHEMA_SOURCE_MAX_CHARS, \
                "第二层被喂了截断的 HTML，字段会静默少一半"
            assert kwargs["offset"] == 0, url

    def test_the_fan_out_runs_six_at_a_time_and_no_more(self, monkeypatch):
        """并发是这一颗参数唯一会打疼站点的地方：30 条链接不能变成 30 个同时请求。"""
        calls: list = []
        many = "".join(f'<div class="c"><a href="/t/{i}">第{i}项</a></div>'
                       for i in range(30))
        pages = {LIST_URL: f"<html><body>{many}</body></html>"}
        pages.update({f"https://list.test/t/{i}": DETAIL["https://list.test/t/1"]
                      for i in range(30)})
        live = _stub_pages(monkeypatch, pages, calls=calls,
                           max_live=nested_mod.NESTED_CONCURRENCY)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".c", nested_fields=FIELDS,
                   nested_max=30, cache_ttl=0)
        assert len(_payload(out)["nested"]) == 30
        assert live["peak"] <= nested_mod.NESTED_CONCURRENCY


class TestWhatTheCapHid:
    """三颗参数最危险的失败不是报错，是**安静地少给**。"""

    def _twelve(self):
        return LIST_URL, "".join(
            f'<div class="c"><a href="/t/{i}">第{i}项</a></div>' for i in range(12))

    def test_default_eight_says_it_left_four_behind(self, monkeypatch):
        calls: list = []
        url, cards = self._twelve()
        pages = {url: f"<html><body>{cards}</body></html>"}
        pages.update({f"https://list.test/t/{i}": DETAIL["https://list.test/t/1"]
                      for i in range(12)})
        _stub_pages(monkeypatch, pages, calls=calls)
        _stub_robots(monkeypatch)

        out = _run(url=url, nested_selector=".c", nested_fields=FIELDS, cache_ttl=0)

        assert len(_payload(out)["nested"]) == nested_mod.DEFAULT_NESTED_ITEMS
        assert "truncation=nested_budget" in out.summary, \
            f"8 条被当成全部交付了：{out.summary}"
        assert "8 of 12" in out.summary

    def test_the_ceiling_is_stated_in_the_note(self, monkeypatch):
        """封顶到 30 也要说 —— 否则 'nested_max=500' 读起来像被忽略了。"""
        calls: list = []
        cards = "".join(f'<div class="c"><a href="/t/{i}">x</a></div>'
                        for i in range(40))
        pages = {LIST_URL: f"<html><body>{cards}</body></html>"}
        pages.update({f"https://list.test/t/{i}": DETAIL["https://list.test/t/1"]
                      for i in range(40)})
        _stub_pages(monkeypatch, pages, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".c", nested_fields=FIELDS,
                   nested_max=500, cache_ttl=0)
        assert len(_payload(out)["nested"]) == nested_mod.MAX_NESTED_ITEMS
        assert str(nested_mod.MAX_NESTED_ITEMS) in out.summary

    def test_rungs_and_per_layer_counts_reach_the_summary(self, monkeypatch):
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card", nested_fields=FIELDS,
                   cache_ttl=0)
        assert "rungs=2" in out.summary, out.summary
        assert "ok 2" in out.summary

    def test_a_second_layer_that_returned_nothing_is_not_a_success(self, monkeypatch):
        """外壳在、内容全空：这不是"抽到了 0 个作品"，是选择器不对。"""
        calls: list = []
        pages = {LIST_URL: LIST_HTML,
                 "https://list.test/t/1": '<html><body><div class="other">x</div></body></html>',
                 "https://list.test/t/2": '<html><body><div class="other">y</div></body></html>'}
        _stub_pages(monkeypatch, pages, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card", nested_fields=FIELDS,
                   cache_ttl=0)
        assert "nested_empty" in out.error, out.error
        assert out.content_ok is False


class TestNothingWasSentSilently:

    def test_zero_links_matched_is_its_own_error(self, monkeypatch):
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".nope", nested_fields=FIELDS,
                   cache_ttl=0)
        assert out.error.startswith("nested_no_match"), out.error
        assert [u for u, _ in calls] == [LIST_URL], "0 命中还去发第二层请求"
        # 两条出路都得在场上：选择器写窄了，和这页是壳。
        assert "force_fetcher='stealthy'" in out.next_action

    def test_a_shell_first_layer_is_handed_back_not_flattened(self, monkeypatch):
        """第一层没读到 DOM 时，扇出不能造一个"0 条记录"的成功。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: ""}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card", nested_fields=FIELDS,
                   cache_ttl=0)
        assert [u for u, _ in calls] == [LIST_URL]
        assert out.extracted_type != "structured"

    def test_nested_max_zero_reads_this_page_only(self, monkeypatch):
        calls: list = []
        # 带上 schema 是为了让这条调用走到 `_apply_chunking`：summary 是
        # `_with_agent_hints` 在那一步算的，普通 markdown 读法由取回层自己收尾。
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=FIELDS, nested_max=0,
                   schema={"properties": {"t": {"selector": ".topic-card"}}},
                   cache_ttl=0)
        assert [u for u, _ in calls] == [LIST_URL]
        assert "nested_max=0" in out.summary, out.summary
        assert "nested" not in out.content[0]

    def test_a_disallowed_second_layer_link_sends_no_request(self, monkeypatch):
        """礼貌跟着扇出搬进来了：robots 拒的那条一个字节都不发，并且说出来。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch, disallowed=("https://list.test/t/2",))

        out = _run(url=LIST_URL, nested_selector=".topic-card", nested_fields=FIELDS,
                   cache_ttl=0)

        fetched = [u for u, _ in calls]
        assert "https://list.test/t/2" not in fetched, "robots 禁的路径被扇出去了"
        records = _payload(out)["nested"]
        blocked = [r for r in records if r.get("error") == "robots_disallowed"]
        assert [r["url"] for r in blocked] == ["https://list.test/t/2"]
        assert "robots_skipped 1" in out.summary, out.summary

    def test_a_bad_nested_selector_stops_before_any_request(self, monkeypatch):
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector="div" * 4000,
                   nested_fields=FIELDS, cache_ttl=0)
        assert calls == [], "注入守卫没跟着进 nested_selector"
        assert "invalid nested_*" in out.error

    def test_a_field_selector_is_guarded_like_a_schema_one(self, monkeypatch):
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=[{"name": "作品", "selector": ".x" * 4000}],
                   cache_ttl=0)
        assert calls == []
        assert "invalid nested_*" in out.error

    @pytest.mark.parametrize("extra", [
        pytest.param({"urls": ["https://other.test/"]}, id="urls"),
        pytest.param({"cdp_url": "http://127.0.0.1:9222"}, id="cdp"),
        pytest.param({"actions": [{"click": ".more"}]}, id="actions"),
    ])
    def test_a_combination_that_would_silently_drop_one_side_refuses(
            self, monkeypatch, extra):
        """三种组合都会让一半场悄悄没跑。闸口在请求之前，不在响应之后。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=FIELDS, cache_ttl=0, **extra)
        assert calls == [], extra
        assert "cannot be combined" in out.error, out.error


class TestNestedFieldsSpelling:

    def test_the_map_spelling_and_the_json_string_are_the_same_call(self, monkeypatch):
        """两种写法都到得了同一个 properties；`type: array` 才是"每一项都要"。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)

        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields={"作品": ".work-item a"}, cache_ttl=0)
        assert out.error == "", out.error
        assert _payload(out)["nested"][0]["作品"] == "作品A"

        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=json.dumps(
                       [{"name": "作品", "selector": ".work-item a",
                         "type": "array"}]), cache_ttl=0)
        assert out.error == "", out.error
        assert _payload(out)["nested"][0]["作品"] == ["作品A", "作品B"]

    def test_a_field_without_a_selector_refuses_rather_than_returning_empties(
            self, monkeypatch):
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=[{"name": "作品"}], cache_ttl=0)
        assert calls == []
        assert "needs a non-empty 'selector'" in out.error

    def test_selector_without_fields_is_refused(self, monkeypatch):
        """8 个空对象不是答案，而"没给字段规格"的默认行为如果就是扇出去抽空，
        调用方会以为这站没内容。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL, nested_selector=".topic-card", cache_ttl=0)
        assert calls == []
        assert "needs nested_fields=" in out.error

    def test_too_many_fields_is_refused_before_the_fan_out(self, monkeypatch):
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        fields = [{"name": f"f{i}", "selector": ".x"} for i in
                  range(nested_mod.MAX_NESTED_FIELDS + 1)]
        out = _run(url=LIST_URL, nested_selector=".topic-card",
                   nested_fields=fields, cache_ttl=0)
        assert calls == []
        assert "the cap is 12" in out.error


class TestSchemaStillWorksAlongside:

    def test_schema_on_the_first_page_and_nested_side_by_side(self, monkeypatch):
        """给了 schema 又给 nested_ 时，两件事都该做：第一层按 schema 抽，第二层扇出去。"""
        calls: list = []
        _stub_pages(monkeypatch, {LIST_URL: LIST_HTML, **DETAIL}, calls=calls)
        _stub_robots(monkeypatch)
        out = _run(url=LIST_URL,
                   schema={"properties": {"cards": {
                       "type": "array", "selector": ".topic-card",
                       "properties": {"href": {"selector": "a",
                                               "attribute": "href"}}}}},
                   nested_selector=".topic-card", nested_fields=FIELDS,
                   cache_ttl=0)
        payload = _payload(out)
        assert "nested" in payload
        # 第一层的 schema 数的是 DOM 命中（5 张卡片，含没 href 的和 javascript: 的），
        # 第二层的记录是去重后的可抓链接（2 条）—— 两个数各自回答各自的问题。
        assert [c["href"] for c in payload["cards"]] == [
            "/t/1", "/t/2", "", "/t/1", "javascript:void(0)"]
        assert len(payload["nested"]) == 2
