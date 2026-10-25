"""web_act 的三颗新动作（2026-10-25，对标 playwright-mcp）：select / navigate / pdf。

为什么是这三颗——每一颗都对着「此前没有一条路」的形状：

- ``select``：Playwright 的 fill 对 <select> 直接抛错（它只认 input/textarea/
  contenteditable），而 click 打开的是操作系统的原生下拉、没有可点的 DOM。
  「在国家下拉框里选中国」在这个工具面上原来办不成（playwright-mcp 的
  browser_select_option 是它 Core 面的一颗）。
- ``navigate``：链首总要重新导航，重导航拿到的是一张**新**页面（表单清空、
  滚动归零、SPA 内存态重置）；历史里那一页保存着这一切。playwright-mcp 的
  browser_navigate_back 同款。
- ``pdf``：截图交像素，pdf 交矢量——文字可选中、doc_parse 能读回来。
  playwright-mcp 把它放在 --caps=pdf 后面；dhole 的动作面没有 caps 概念，
  它就是一颗动作。

边界（与既有纪律同款）：select 的失配把选项清单交回（问页面，不问调用方
第二次）；pdf 只落盘、字节不进 transcript，headed/CDP 那两扇窗打不了印要说
具名；navigate 没有"硬失败"——历史里没有那一格是事实，不是错误。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp.actions import (
    _SELECT_RESOLVE_JS, _validate_actions, action_label, build_page_action,
)

PDF_BYTES = b"%PDF-1.4 fake print of the settled page"


# ─── 假页面：只实现这三颗动作问到的那些面 ─────────────────────────────


class _SelectLocator:
    """evaluate(_SELECT_RESOLVE_JS) 的真身由页面那一侧回答；这里按动作给的
    wants 现算，与真 JS 同一套语义（value 先、label 后）。"""

    def __init__(self, page, options, multiple=False, tag="select"):
        self._page = page
        self._options = options  # [(label, value), ...]
        self._multiple = multiple
        self._tag = tag
        self.chosen: list | None = None

    @property
    def first(self):
        return self

    async def evaluate(self, js, arg=None):
        if js is _SELECT_RESOLVE_JS or "querySelectorAll('option')" in js:
            wants = arg or []

            def pick(w):
                for label, value in self._options:
                    if value == w:
                        return [value, label]
                for label, value in self._options:
                    if label == w:
                        return [value, label]
                return None

            return {"tag": self._tag, "multiple": self._multiple,
                    "resolved": [pick(w) for w in wants],
                    "options": [[lab, val] for lab, val in self._options[:12]]}
        raise AssertionError(f"unexpected locator evaluate: {str(js)[:60]}")

    async def select_option(self, value=None, **kw):
        self.chosen = list(value or [])


class _Page:
    """select / navigate / pdf 三颗动作 + 链的收尾（DOM 安静）问到的那面。"""

    def __init__(self, locator=None):
        self.url = "https://example.com/form"
        self._locator = locator
        #: go_back/go_forward 的出队序列：(返回值, 导航到的 URL)。URL 变不变由
        #: 队列说了算 —— 执行器按"URL 动没动"交回执（真机实测：这条驱动的历史
        #: 导航即使真的动了也解析成 None，None 不是"无历史"的证据）。
        self.history: list[tuple[object, str]] = []
        self.back_calls: list[dict] = []
        self.forward_calls: list[dict] = []
        self.pdf_calls: list[dict] = []
        self.pdf_error: Exception | None = None
        self._listeners: list = []

    def locator(self, sel):
        return self._locator

    def on(self, event, handler):
        self._listeners.append(handler)

    def remove_listener(self, event, handler):
        if handler in self._listeners:
            self._listeners.remove(handler)

    async def evaluate(self, js, arg=None):
        # _DOM_MARK_JS：交回稳定的三元组，_settle_quiet 两拍就安静。
        if "location.href" in js:
            return [self.url, 42, 100]
        raise AssertionError(f"unexpected page evaluate: {str(js)[:60]}")

    async def wait_for_timeout(self, ms):
        pass

    async def go_back(self, **kw):
        self.back_calls.append(kw)
        if not self.history:
            return None
        went, url = self.history.pop(0)
        self.url = url
        return went

    async def go_forward(self, **kw):
        self.forward_calls.append(kw)
        if not self.history:
            return None
        went, url = self.history.pop(0)
        self.url = url
        return went

    async def pdf(self, **kw):
        self.pdf_calls.append(kw)
        if self.pdf_error is not None:
            raise self.pdf_error
        return PDF_BYTES


async def _run(page, actions):
    pa = build_page_action(actions)
    await pa(page)
    return pa


# ─── 校验 ───────────────────────────────────────────────────────────────


class TestValidation:

    def test_a_bare_option_string_normalizes_to_the_list_shape(self):
        (a,) = _validate_actions([
            {"select": {"selector": "#country", "option": "CN"}}])
        assert a == {"select": {"selector": "#country", "option": ["CN"]}}

    def test_a_list_of_options_passes_through(self):
        (a,) = _validate_actions([
            {"select": {"selector": "#top", "option": ["a", "b"]}}])
        assert a["select"]["option"] == ["a", "b"]

    def test_select_without_option_is_refused_by_name(self):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"select": {"selector": "#country"}}])
        assert "option" in str(e.value)

    @pytest.mark.parametrize("bad", ["", "  ", [], [""], ["a", 5], 5, {"a": 1}])
    def test_select_refuses_empty_and_non_string_options(self, bad):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"select": {"selector": "#c", "option": bad}}])
        assert "option" in str(e.value)

    @pytest.mark.parametrize("good", ["back", "forward"])
    def test_navigate_takes_exactly_two_verbs(self, good):
        assert _validate_actions([{"navigate": good}]) == [{"navigate": good}]

    @pytest.mark.parametrize("bad", ["Back", "reload", 1, True, None])
    def test_navigate_refuses_everything_else(self, bad):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"navigate": bad}])
        assert "back" in str(e.value) and "forward" in str(e.value)

    def test_pdf_takes_exactly_save_to(self, tmp_path):
        target = tmp_path / "out.pdf"
        (a,) = _validate_actions([{"pdf": {"save_to": str(target)}}])
        assert a["pdf"]["save_to"] == str(target)

    @pytest.mark.parametrize("bad", [{}, {"save_to": "x.txt"},
                                     {"save_to": "a.pdf", "quality": 80},
                                     {"save_to": ""}, "out.pdf"])
    def test_pdf_refuses_other_shapes_and_suffixes(self, bad, tmp_path):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"pdf": bad}])
        assert "pdf" in str(e.value)

    def test_pdf_refuses_restricted_directories(self, tmp_path):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"pdf": {"save_to": "~/.ssh/leak.pdf"}}])
        assert "restricted" in str(e.value)


# ─── select 执行器 ───────────────────────────────────────────────────────


class TestSelectExecutor:

    @pytest.mark.asyncio
    async def test_a_value_match_selects_and_the_receipt_carries_both_names(self):
        loc = _SelectLocator(None, [("中国", "CN"), ("日本", "JP")])
        pa = await _run(_Page(loc), [
            {"select": {"selector": "#country", "option": "CN"}}])
        (row,) = pa.outcomes
        assert row["ok"] is True, row
        assert loc.chosen == ["CN"]
        assert row["selected"] == [{"value": "CN", "label": "中国"}]

    @pytest.mark.asyncio
    async def test_a_label_match_falls_back_when_the_value_is_a_machine_code(self):
        loc = _SelectLocator(None, [("中国", "+86"), ("日本", "+81")])
        pa = await _run(_Page(loc), [
            {"select": {"selector": "#tel", "option": "中国"}}])
        (row,) = pa.outcomes
        assert row["ok"] is True, row
        assert loc.chosen == ["+86"], "标签匹配是 value 匹配不中时的第二条路"

    @pytest.mark.asyncio
    async def test_a_miss_answers_with_the_menu_not_a_shrug(self):
        loc = _SelectLocator(None, [("中国", "CN"), ("日本", "JP")])
        pa = await _run(_Page(loc), [
            {"select": {"selector": "#c", "option": "韩国"}}])
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "韩国" in row["error"]
        assert "中国=CN" in row["error"] and "日本=JP" in row["error"], row["error"]
        assert loc.chosen is None, "失配就不许选任何东西"

    @pytest.mark.asyncio
    async def test_a_list_on_a_single_choice_select_is_refused_by_name(self):
        loc = _SelectLocator(None, [("红", "r"), ("绿", "g")], multiple=False)
        pa = await _run(_Page(loc), [
            {"select": {"selector": "#c", "option": ["r", "g"]}}])
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "ONE option" in row["error"]
        assert loc.chosen is None

    @pytest.mark.asyncio
    async def test_a_list_on_a_real_multi_select_selects_them_all(self):
        loc = _SelectLocator(None, [("红", "r"), ("绿", "g")], multiple=True)
        pa = await _run(_Page(loc), [
            {"select": {"selector": "#colors", "option": ["r", "绿"]}}])
        (row,) = pa.outcomes
        assert row["ok"] is True, row
        assert loc.chosen == ["r", "g"], "两种拼法各命中一颗"
        assert row["selected"] == [{"value": "r", "label": "红"},
                                   {"value": "g", "label": "绿"}]

    @pytest.mark.asyncio
    async def test_targeting_something_else_names_the_door_that_fits(self):
        loc = _SelectLocator(None, [], tag="input")
        pa = await _run(_Page(loc), [
            {"select": {"selector": "#name", "option": "x"}}])
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "<input>" in row["error"] and "fill" in row["error"]


# ─── navigate 执行器 ─────────────────────────────────────────────────────


class TestNavigateExecutor:

    @pytest.mark.asyncio
    async def test_back_lands_on_the_held_page_and_reports_the_url(self):
        page = _Page()
        page.history = [(None, "https://example.com/form")]  # None 也照样导航：
        page.url = "https://example.com/form/about"          # 真机实测，commit 档
        pa = await _run(page, [{"navigate": "back"}])
        (row,) = pa.outcomes
        assert row["ok"] is True, row
        assert row["navigate"] == "back"
        assert row["url"] == "https://example.com/form"
        assert "page_unchanged" not in row
        assert page.back_calls[0].get("wait_until") == "commit", (
            "wait_until=commit：SPA 的 popstate 路由没有 load 事件，等 load "
            "白等到超时（真机实测默认档挂满 30s）")

    @pytest.mark.asyncio
    async def test_the_page_not_moving_is_reported_as_an_observed_fact(self):
        """历史里没有那一格（或同文档入口什么都没改）：回执只说观测事实。

        真机实测：这条驱动的历史导航即使真的动了也解析成 None —— None 不是
        「无历史」的证据，所以这里不声称原因，只交 URL 与 page_unchanged。
        """
        page = _Page()  # 空队列：go_back 解析 None，URL 不动
        pa = await _run(page, [{"navigate": "forward"}])
        (row,) = pa.outcomes
        assert row["ok"] is True, row
        assert row["page_unchanged"] is True
        assert row["url"] == "https://example.com/form"
        assert "no history entry" not in json.dumps(row)


# ─── pdf 执行器 ──────────────────────────────────────────────────────────


class TestPdfExecutor:

    @pytest.mark.asyncio
    async def test_the_print_lands_on_disk_and_never_on_the_transcript(self, tmp_path):
        page = _Page()
        target = tmp_path / "receipt.pdf"
        pa = await _run(page, [{"pdf": {"save_to": str(target)}}])
        (row,) = pa.outcomes
        assert row["ok"] is True, row
        assert target.read_bytes() == PDF_BYTES
        assert row["saved"] == str(target.resolve())
        assert row["saved_bytes"] == len(PDF_BYTES)
        assert page.pdf_calls[0]["print_background"] is True, (
            "深色主题与高亮行承载信息，背景不是装饰")
        assert "image_b64" not in row and "content" not in row

    @pytest.mark.asyncio
    async def test_a_visible_window_cannot_print_and_says_so_by_name(self, tmp_path):
        page = _Page()
        page.pdf_error = RuntimeError(
            "Page.pdf() is only supported in headless mode")
        pa = await _run(page, [{"pdf": {"save_to": str(tmp_path / "x.pdf")}}])
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "hidden browser" in row["error"]
        assert "headed=true" in row["error"]


# ─── 回执那一行 ──────────────────────────────────────────────────────────


class TestLabels:

    def test_select_names_the_option_and_the_target(self):
        assert action_label(
            {"select": {"selector": "#c", "option": ["CN"]}}) \
            == "select 'CN' in '#c'"

    def test_navigate_names_the_verb(self):
        assert action_label({"navigate": "back"}) == "navigate back"

    def test_pdf_names_the_destination(self):
        label = action_label({"pdf": {"save_to": "out/receipt.pdf"}})
        assert label.startswith("pdf ") and "receipt.pdf" in label
