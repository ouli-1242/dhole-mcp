"""test_actions_2.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import json
import re
import pytest
from dhole_mcp import guide, wire
from dhole_mcp.actions import MAX_ACTION_READ_CHARS, _VALID_KEYS, _validate_actions, action_label, build_page_action
from dhole_mcp.server import MasterFetchServer
from pathlib import Path
from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.actions import actions_want_visual
from dhole_mcp.security import SecurityError, allow_private_scope, validate_url
from dhole_mcp.server import ResponseModel
from dhole_mcp.actions import _READ_PROPERTY_JS, _TAG_JS


# ============================================================================
# 来源：test_a_challenge_read_in_one_chain_is_answered_in_that_same_chain.py
# ============================================================================
# 同一条链里读到的挑战，必须能在同一条链里答掉 —— 跨调用重试答的是上一页的答案。

# ─── 假页面：只长这一次判据需要的器官 ────────────────────────────────


class _El:
    def __init__(self, text="", attrs=None, name=""):
        self.text = text
        self.attrs = dict(attrs or {})
        self.name = name
        self.filled: list[str] = []
        self.hovered = 0

    async def inner_text(self, timeout=None):
        return self.text

    async def get_attribute(self, attr):
        return self.attrs.get(attr)

    async def fill(self, value, timeout=None):
        self.filled.append(value)

    async def evaluate(self, js, arg=None):
        return self.name


class _Loc:
    def __init__(self, el):
        self._el = el

    @property
    def first(self):
        return self

    def nth(self, i):
        return self

    async def inner_text(self, timeout=None):
        return await self._el.inner_text()

    async def get_attribute(self, attr):
        return await self._el.get_attribute(attr)

    async def fill(self, value, timeout=None):
        await self._el.fill(value)

    async def evaluate(self, js, arg=None):
        return await self._el.evaluate(js)


class _Page:
    def __init__(self, by_selector: dict[str, _El]):
        self.els = by_selector
        self.missing = "no such element"

    def locator(self, sel):
        if sel not in self.els:
            raise RuntimeError(f"waiting for locator {sel!r}")
        return _Loc(self.els[sel])


def _run(chain, page):
    pa = build_page_action(chain)
    asyncio.run(pa(page))
    return pa.outcomes


CHAIN = [{"read": {"selector": "#challenge", "as": "code"}},
         {"fill": {"selector": "#answer", "text": "{=code}"}}]


# ─── 1 · 校验：形状、名字、顺序 ────────────────────────────────────

def test_read_is_a_registered_action():
    assert "read" in _VALID_KEYS


def test_a_bare_selector_and_the_dict_form_both_normalize():
    (a,) = _validate_actions([{"read": "#challenge"}])
    assert a == {"read": {"selector": "#challenge"}}
    (b,) = _validate_actions([{"read": {"index": 3, "expect": "字符", "attribute": "value"}}])
    assert b == {"read": {"index": 3, "expect": "字符", "attribute": "value"}}


def test_a_target_is_mandatory_and_one_of_selector_or_index():
    with pytest.raises(ValueError, match="exactly one of"):
        _validate_actions([{"read": {"selector": "#a", "index": 1}}])
    with pytest.raises(ValueError, match="read"):
        _validate_actions([{"read": {}}])


@pytest.mark.parametrize("bad", ["Bad Name", "a" * 40, "with space", 7, ""])
def test_as_names_are_refused_by_name(bad):
    with pytest.raises(ValueError, match="read.as"):
        _validate_actions([{"read": {"selector": "#c", "as": bad}}])


@pytest.mark.parametrize("bad", ["onmouseover=x", "", "a b", 7, "x" * 60])
def test_attribute_names_are_refused_by_name(bad):
    with pytest.raises(ValueError, match="read.attribute"):
        _validate_actions([{"read": {"selector": "#c", "attribute": bad}}])


def test_a_reference_to_nothing_is_refused_instead_of_being_filled_verbatim():
    """这一条是整刀的支点：写错的引用若原样填进表单，回执会显示一次成功的 fill。"""
    with pytest.raises(ValueError) as e:
        _validate_actions([{"fill": {"selector": "#a", "text": "{=code}"}}])
    assert "{=code}" in str(e.value)
    assert "no" in str(e.value) and "read" in str(e.value)


def test_a_reference_only_reaches_backwards_not_forwards():
    """先 fill 后 read 是写反了顺序，不是"稍后就绪"。"""
    with pytest.raises(ValueError, match="no earlier action stored"):
        _validate_actions([{"fill": {"selector": "#a", "text": "{=code}"}},
                           {"read": {"selector": "#c", "as": "code"}}])


def test_a_refused_reference_names_what_is_available():
    with pytest.raises(ValueError) as e:
        _validate_actions([{"read": {"selector": "#c", "as": "hint"}},
                           {"fill": {"selector": "#a", "text": "{=code}"}}])
    assert "available" in str(e.value) and "hint" in str(e.value)


def test_a_plain_brace_is_text_not_a_reference():
    """`{code}` 是模板、JSON、说明文字里常见的字面量 —— 引用要写 `{=code}`。"""
    (a,) = _validate_actions([{"fill": {"selector": "#a", "text": "literal {code} here"}}])
    assert a["fill"]["text"] == "literal {code} here"


def test_the_label_says_what_was_read_and_what_it_was_named():
    assert action_label({"read": {"selector": "#c", "as": "code"}}) == "read '#c' as 'code'"
    assert action_label({"read": {"index": 2, "attribute": "value"}}) == "read [2] @value"


# ─── 2 · 执行：读进回执、填进表单 ──────────────────────────────────

def test_a_read_lands_in_the_receipt_and_a_later_fill_uses_it():
    page = _Page({"#challenge": _El(text="  K7Q2Z \n"), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert [r["ok"] for r in rows] == [True, True]
    assert rows[0]["read"] == "K7Q2Z"
    assert rows[0]["read_as"] == "code"
    assert page.els["#answer"].filled == ["K7Q2Z"]


def test_the_value_that_goes_in_is_the_one_this_page_showed():
    """两枚挑战、两次导航：第二条链填的是第二条页面上的那一枚，不是上一页的答案。

    这一条钉的就是那句 "read-and-retry rounds" 做不到的事 —— 它做不到的原因不在重试，
    在每次重试都换一枚；在同一条链里读到并填回，才第一次让"看到的"与"答的"是同一枚。
    """
    first = _Page({"#challenge": _El(text="AAAAA"), "#answer": _El()})
    _run(CHAIN, first)
    second = _Page({"#challenge": _El(text="ZZZZZ"), "#answer": _El()})
    _run(CHAIN, second)
    assert first.els["#answer"].filled == ["AAAAA"]
    assert second.els["#answer"].filled == ["ZZZZZ"]


def test_text_around_the_reference_survives_the_substitution():
    page = _Page({"#challenge": _El(text="K7Q2Z"), "#answer": _El()})
    _run([{"read": {"selector": "#challenge", "as": "code"}},
          {"fill": {"selector": "#answer", "text": "prefix-{=code}-suffix"}}], page)
    assert page.els["#answer"].filled == ["prefix-K7Q2Z-suffix"]


def test_an_attribute_read_reaches_the_fill_too():
    """框的值不是元素的文本 —— 读它要 `attribute`，这一格把那句话演一遍。"""
    page = _Page({"#tok": _El(text="", attrs={"value": "TK-9"}), "#answer": _El()})
    rows = _run([{"read": {"selector": "#tok", "attribute": "value", "as": "t"}},
                 {"fill": {"selector": "#answer", "text": "{=t}"}}], page)
    assert rows[0]["read"] == "TK-9"
    assert page.els["#answer"].filled == ["TK-9"]


# ─── 3 · 读数不在时：那一行怎么说 ─────────────────────────────────

def test_an_empty_text_read_fails_the_step_and_names_the_other_two_doors():
    page = _Page({"#challenge": _El(text="   "), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[0]["ok"] is False
    err = rows[0]["error"]
    # 那扇门从前叫 `attribute`，而 attribute 读的是标签上那一层 —— 填过的框里它报的是
    # 旧值（实测：fill 成功、POST 出去的也是新值，这一读却说"还是 1"）。现在它叫
    # `property`。见 tests/test_the_probe_that_reads_a_filled_input_reads_the_value_the_page_holds.py
    assert "property" in err and "value" in err
    assert "screenshot" in err


def test_an_empty_text_read_fills_nothing_and_says_the_reference_did_not_resolve():
    """读失败之后，引用它的那一步不许伪装成功 —— 填进表单的不能是 `{=code}` 那六个字符。"""
    page = _Page({"#challenge": _El(text=""), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[1]["ok"] is False
    assert "metadata.actions" in rows[1]["error"]
    assert page.els["#answer"].filled == []


def test_an_absent_attribute_reports_as_absent_not_as_empty_page_text():
    page = _Page({"#tok": _El(text="无关的正文", attrs={}), "#answer": _El()})
    rows = _run([{"read": {"selector": "#tok", "attribute": "value", "as": "t"}}], page)
    assert rows[0]["ok"] is False
    assert "'value'" in rows[0]["error"]
    assert "screenshot" not in rows[0]["error"]


def test_a_selector_that_matches_nothing_is_the_usual_loud_failure():
    page = _Page({"#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[0]["ok"] is False
    assert "no element" in rows[0]["error"]


# ─── 4 · 帽：说给读的人听，但不砍给表单 ────────────────────────────

def test_a_long_read_is_capped_in_the_receipt_and_says_so_with_the_original_length():
    long = "甲" * (MAX_ACTION_READ_CHARS + 37)
    page = _Page({"#challenge": _El(text=long), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert rows[0]["read"] == long[:MAX_ACTION_READ_CHARS]
    assert rows[0]["read_truncated"] is True
    assert rows[0]["read_chars_total"] == len(long.strip())


def test_the_cap_announces_itself_only_when_it_bites():
    page = _Page({"#challenge": _El(text="SHORT"), "#answer": _El()})
    rows = _run(CHAIN, page)
    assert "read_truncated" not in rows[0] and "read_chars_total" not in rows[0]


def test_the_register_holds_the_whole_value_not_the_capped_head():
    """回执给的是帽内的前一段，填回表单的必须是全文：截断是显示的事，不是答案的事。"""
    long = "X" * (MAX_ACTION_READ_CHARS + 500)
    page = _Page({"#challenge": _El(text=long), "#answer": _El()})
    _run(CHAIN, page)
    assert page.els["#answer"].filled == [long]


def test_a_substitution_that_would_outgrow_the_field_is_refused_by_name():
    page = _Page({"#challenge": _El(text="X" * 4_600), "#answer": _El()})
    rows = _run([{"read": {"selector": "#challenge", "as": "code"}},
                 {"fill": {"selector": "#answer", "text": ("P" * 600) + "{=code}"}}], page)
    assert rows[1]["ok"] is False
    assert "5,000" in rows[1]["error"]
    assert page.els["#answer"].filled == []


# ─── 5 · 回执上的新键要活着走到线上，并且写在手册里 ─────────────────

def test_the_new_receipt_keys_survive_the_wire_dump():
    """`metadata.actions` 的行是 dict，逐键透传 —— 这条量的是"真的透传"，不是应当如此。"""
    page = _Page({"#challenge": _El(text="K7Q2Z"), "#answer": _El()})
    resp = ResponseModel(url="https://example.com", status=200, content=[])
    pa = build_page_action(CHAIN)
    asyncio.run(pa(page))
    from dhole_mcp.quality import _report_action_outcomes
    _report_action_outcomes(resp, pa)
    dumped = wire._wire_dump(resp)
    row = dumped["metadata"]["actions"][0]
    assert row["read"] == "K7Q2Z" and row["read_as"] == "code"


def test_the_read_action_is_named_where_a_caller_decides_what_to_send():
    """常驻层那两份（动作清单 + 工具描述）都要说得出这件事。

    清单给名字，描述给"读了之后怎么用"那一对 —— `{=code}` 的记号从前不存在，
    只见过旧 wire 的调用方会像体测里那样，把截图当成"下一步能照着填"的东西。
    """
    td = next(d for d in MasterFetchServer._TOOL_DEFS if d["name"] == "web_act")
    listed = td["inputSchema"]["properties"]["actions"]["description"]
    tool = td["description"]
    assert re.search(r"(?<![\w-])read(?![\w-])", listed), listed
    assert '"read"' in tool and "{=code}" in tool, tool


def test_the_impossible_promise_is_not_back_on_the_wire():
    """两句做不到的话不许回来 —— 它们是本刀撤下来的东西，不是风格问题。

    `read-and-retry rounds`：每次调用都重新导航，第二轮读的是新的一枚，填的是上一轮的
    答案（实测靶站 `/challenge/text` 两次导航的 token 不同，见 `tests/
    test_a_chain_answers_the_challenge_of_the_page_it_is_standing_on.py`）。
    `a CAPTCHA you are about to type`：截图要等整条链跑完才交得到调用方眼前，链里没有
    一步能照着它打字。两句都是**承诺了实现给不了的杠杆**，而调用方按它花的是一整轮
    浏览器时间（体测里那一格烧掉 3 次调用）。
    """
    blob = json.dumps(MasterFetchServer._TOOL_DEFS, ensure_ascii=False)
    for banned in ("read-and-retry", "about to type"):
        assert banned not in blob, f"wire 上又出现那句做不到的承诺: {banned!r}"


def test_the_reference_shape_is_in_a_volume_the_guide_serves():
    """`{=名字}` 与那两扇替代门（`attribute` / `screenshot`）得写在按需取的那一卷里。

    常驻层只带得出动作名（那颗 296 字符的闸在前一刀刚试过），所以这一卷是调用方
    查"怎么把读回来的东西用掉"的地方 —— 它不在，能力就等于只在源码里。
    """
    _header, behavior = guide.read("behavior")
    for needle in (r"\{=code\}", r"(?<![\w.\"])as(?![\w\"])",
                   r"(?<![\w.\"])attribute(?![\w\"])",
                   "for a challenge drawn as an image",
                   "already gone on the next"):
        assert re.search(needle, behavior), needle


def test_the_receipt_keys_are_documented_where_a_reader_looks_up_a_field():
    """回执上的新键进 fields.md —— 那才是查"这一行为什么这么写"的地方。"""
    from dhole_mcp import guide
    fields = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
    for needle in ("read_truncated", "read_chars_total", "read_as"):
        assert needle in fields, needle


# ============================================================================
# 来源：test_eval_fixes_g35.py
# ============================================================================
# G35 —— evals/ 模拟测试暴露的四项缺陷的回归测试。

class _Stop(Exception):
    """Short-circuit a call at the seam under test."""


def _loopback_is_fetchable() -> bool:
    """True when the SSRF guard currently lets a loopback URL through."""
    try:
        validate_url("http://127.0.0.1:8399/")
    except SecurityError:
        return False
    return True


# ─── D1：截图动作必须让资源拦截让路 ──────────────────────────────────────

class TestAScreenshotActionKeepsPageResources:
    """D1。截图是唯一"产品就是渲染结果"的动作，而 stealthy 路径默认关掉
    image/stylesheet/font 提速——在拦截下截元素，得到的是破图占位符，不是
    调用方要看的那张二维码。"""

    @staticmethod
    def _kwargs_seen(monkeypatch, actions):
        seen: dict = {}

        async def fake_force_fetch(self, *args, **kwargs):
            seen.update(kwargs)
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "_force_fetch",
                            fake_force_fetch)
        with pytest.raises(_Stop):
            asyncio.run(MasterFetchServer().web_read(
                url="https://example.com/", actions=actions))
        return seen

    def test_a_screenshot_action_turns_resource_blocking_off(self, monkeypatch):
        monkeypatch.setenv("DHOLE_MULTIMODAL", "1")
        seen = self._kwargs_seen(monkeypatch, [{"screenshot": {"selector": "#qr"}}])
        assert seen["disable_resources"] is False

    def test_actions_without_a_screenshot_keep_the_speed_shortcut(self, monkeypatch):
        seen = self._kwargs_seen(monkeypatch, [{"click": "#more"}])
        assert seen["disable_resources"] is True

    def test_a_snapshot_only_chain_keeps_the_speed_shortcut(self, monkeypatch):
        seen = self._kwargs_seen(monkeypatch, [{"snapshot": True}, {"scroll": 3}])
        assert seen["disable_resources"] is True


class TestActionsWantVisual:
    """D1 的判定函数本身。扫的是**原始列表**，与 `_validate_actions` 同一个键
    测试，所以能在验证之前跑，也不会与解析器分叉。"""

    @pytest.mark.parametrize("actions,expected", [
        (None, False),
        ([], False),
        ("not a list", False),
        ({"screenshot": {"selector": "#qr"}}, False),  # 不是列表
        ([{"click": "#a"}], False),
        ([{"snapshot": True}, {"scroll": 3}], False),
        ([{"screenshot": {"selector": "#qr"}}], True),
        ([{"scroll": 3}, {"screenshot": {"selector": "#qr"}}], True),
        ([{"screenshot": "#qr"}], True),
        (["not a dict", {"screenshot": {"selector": "#qr"}}], True),
    ])
    def test_the_scan(self, actions, expected):
        assert actions_want_visual(actions) is expected


# ─── D2：索引寻址的动作标签 ──────────────────────────────────────────────

class TestIndexAddressedActionLabels:
    """D2。`_act_target` 把 index 寻址归一化成 `{"index": N}`（**不带 selector
    键**），而 `action_label` 的 click/submit 分支仍按 `.get("selector")` 取值，
    于是标签渲染成 `click None` / `submit None (unconfirmed)`。"""

    def test_a_click_by_index_is_named_by_its_index(self):
        assert action_label({"click": {"index": 0}}) == "click [0]"

    def test_a_click_by_index_keeps_the_expect_name(self):
        assert action_label({"click": {"index": 0, "expect": "登录"}}) == "click [0] ~登录"

    def test_a_submit_by_index_names_its_target(self):
        assert action_label({"submit": {"index": 2, "confirm": False}}) == \
            "submit [2] (unconfirmed)"

    def test_a_confirmed_submit_drops_the_gate_note(self):
        assert action_label({"submit": {"index": 2, "confirm": True}}) == "submit [2]"

    def test_a_selector_addressed_submit_is_unchanged(self):
        assert action_label({"submit": {"selector": "#buy", "confirm": False}}) == \
            "submit '#buy' (unconfirmed)"

    @pytest.mark.parametrize("action", [
        {"click": {"index": 0}},
        {"click": {"index": 7, "expect": "结算"}},
        {"fill": {"index": 1, "text": "x"}},
        {"press": {"index": 2, "key": "Enter"}},
        {"submit": {"index": 3}},
        {"submit": {"index": 3, "confirm": True}},
    ])
    def test_no_index_addressed_label_ever_says_none(self, action):
        """一个笼统的守卫：`None` 出现在标签里永远是缺陷，不是某一种动作的
        特例——这四行是给下一个新增动作类型准备的。"""
        label = action_label(action)
        assert "None" not in label, label
        assert "[", f"索引没出现在标签里: {label}"


class TestTheAuditRowCarriesTheSubmitTarget:
    """D2 的后果面。`action` 字段是**写操作审计**里唯一说明"提交了哪个元素"
    的东西，所以标签缺陷泄漏进审计比标签本身难看要严重得多。"""

    def test_a_paused_submit_names_its_target_in_the_audit_row(
            self, monkeypatch, tmp_path: Path):
        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))

        result = ResponseModel(url="https://shop.example.com/checkout",
                               status=200, content=[])
        label = action_label({"submit": {"index": 2, "confirm": False}})
        page_action = type("PA", (), {
            "outcomes": [{"action": label, "ok": False,
                          "pending_approval": True, "error": "pending_approval"}],
        })()

        server_mod._report_action_outcomes(result, page_action)

        rows = [json.loads(ln) for ln in
                log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        writes = [r for r in rows if r.get("event") == "write"]
        assert len(writes) == 1
        assert writes[0]["channel"] == "browser_submit"
        assert writes[0]["pending"] is True
        assert "[2]" in writes[0]["action"], writes[0]["action"]
        assert "None" not in writes[0]["action"], writes[0]["action"]


# ─── D3：私有目标口径统一 ────────────────────────────────────────────────

class TestAllowPrivateScope:
    """D3。`allow_private_scope` 是 web_read 那套机制的可复用形态，给两个
    没走 options 装饰器的工具（screenshot / feed_read）用同一套答案，而不是
    再立一套要同步维护的策略。"""

    def test_loopback_is_refused_outside_the_scope(self):
        assert _loopback_is_fetchable() is False

    def test_loopback_is_fetchable_inside_the_scope(self):
        with allow_private_scope(True):
            assert _loopback_is_fetchable() is True

    def test_the_scope_closes_again(self):
        with allow_private_scope(True):
            pass
        assert _loopback_is_fetchable() is False

    def test_a_host_list_does_not_open_all_of_loopback(self):
        with allow_private_scope(["my-service.local"]):
            assert _loopback_is_fetchable() is False

    def test_a_falsy_value_is_a_no_op(self):
        with allow_private_scope(None):
            assert _loopback_is_fetchable() is False


class TestThePrivateTargetOptionReachesBothTools:
    """D3 的接线面。17.0 §3-17 之后 `allow_private` 不再是调用参数：它住在
    cred_defaults 的存储里（`identity.GLOBAL_KEYS`），两个工具各自在**读回它的
    那几行**周围作用域。

    仓库那条不变式（「被转发的选项必须是方法接受的参数」，
    tests/test_crawl_politeness_g27.py）现在反过来守这条刀：退役的参数是陌生键，
    入口必须拒绝而不是吞掉 —— 吞掉一次 `allow_private=true`，调用方就会以为
    作用域开过。
    """

    def test_the_dispatcher_refuses_the_retired_option_instead_of_forwarding_it(
            self, monkeypatch):
        seen: dict = {}

        async def fake_feed_fetch(self, **kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(server_mod.MasterFetchServer, "feed_read",
                            fake_feed_fetch)
        with pytest.raises(ValueError, match="Unsupported argument") as e:
            asyncio.run(MasterFetchServer()._dispatch("feed_read", {
                "urls": ["http://127.0.0.1:8399/feed.xml"], "allow_private": True}))

        assert "allow_private" in str(e.value)
        assert seen == {}, "退役的参数不该再被当成参数转进方法"

    def test_neither_method_carries_the_option_as_a_parameter(self):
        import inspect

        for tool in ("web_act", "feed_read"):
            params = inspect.signature(getattr(MasterFetchServer, tool)).parameters
            assert "allow_private" not in params, \
                f"{tool} 仍在收已经退役的 allow_private 参数"
            assert "allow_private" not in server_mod._TOP_LEVEL_ARGS[tool], \
                f"{tool} 的参数面还宣传着 allow_private"
        # 它现在住在哪：cred_defaults 的全局键（一次设定，两个工具各自读回）。
        assert "allow_private" in identity.GLOBAL_KEYS

    def test_feed_fetch_opens_the_scope_around_validation_and_fetch(self, monkeypatch):
        """存储里的 `allow_private` 要盖住校验**和**抓取两步：只放开校验那一步，
        等于放行了 URL 又在取文档时把它拒掉。"""
        import dhole_mcp.feed as feed_mod

        seen: dict = {}

        async def fake_fetch_feeds(urls, **kwargs):
            seen["urls"] = urls
            seen["loopback_ok"] = _loopback_is_fetchable()
            return []

        monkeypatch.setattr(feed_mod, "fetch_feeds", fake_fetch_feeds)
        identity.set_value("allow_private", True)

        out = asyncio.run(MasterFetchServer().feed_read(
            urls=["http://127.0.0.1:8399/feed.xml"]))

        assert out == []
        assert seen["urls"] == ["http://127.0.0.1:8399/feed.xml"]
        assert seen["loopback_ok"] is True

    def test_feed_fetch_refuses_a_loopback_url_without_the_option(self):
        with pytest.raises(ValueError) as e:
            asyncio.run(MasterFetchServer().feed_read(
                urls=["http://127.0.0.1:8399/feed.xml"]))
        assert "feed_read URL 校验失败" in str(e.value)
        assert "internal" in str(e.value)

    def test_a_capture_action_inherits_the_open_scope_end_to_end(self, monkeypatch):
        """A1 之后截图不再是独立工具：回环上的"截我自己的 dev 服务"走 web_act。
        存储里放行私网时，作用域要开着走到导航那一步（回环页面自己的子资源由
        浏览器路由处理器判定，读同一个上下文变量）——这由 web_read 的既有机制
        保证，这里从派发层探针验证接线仍在。"""
        seen: dict = {}

        async def fake_force_fetch(self, url, fetcher, **kwargs):
            seen["loopback_ok"] = _loopback_is_fetchable()
            raise RuntimeError("probe: stop at the browser")

        monkeypatch.setattr(MasterFetchServer, "_force_fetch", fake_force_fetch)
        monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
        identity.set_value("allow_private", True)

        with pytest.raises(RuntimeError, match="probe"):
            asyncio.run(MasterFetchServer()._dispatch("web_act", {
                "url": "http://127.0.0.1:8399/qr",
                "actions": [{"screenshot": {"selector": "img.qr"}}]}))

        assert seen["loopback_ok"] is True, "导航时作用域必须是开的"

    def test_act_refuses_loopback_without_the_option(self, monkeypatch):
        monkeypatch.setattr(MasterFetchServer, "_force_fetch", self._boom)
        identity.set_value("allow_private", False)
        # 放行缺席时拒在闸门：不抛异常，回的是"一个请求都没发"的拒绝信封。
        content, payload = asyncio.run(MasterFetchServer()._dispatch("web_act", {
            "url": "http://127.0.0.1:8399/qr",
            "actions": [{"screenshot": {"selector": "img.qr"}}]}))
        text = content[0].text
        assert "private" in text or "loopback" in text, text[:200]

    @staticmethod
    def _boom(*a, **kw):
        raise AssertionError("a browser was started for a refused target")


# ─── D4：审计日志的 ok 口径 ──────────────────────────────────────────────

class TestTheCallLogRecordsApplicationLevelFailures:
    """D4。`ok` 是"调用跑完了、没抛异常"，不是"工具报告了成功"。一个被 SSRF
    守卫拒掉的 feed_read 会走正常 envelope 返回，于是日志里记成
    `{"tool": "feed_read", "ok": true, "ms": 22.0}`，拒绝对读者不可见。"""

    def test_a_refusal_envelope_yields_its_error(self):
        payload = {"feeds": [], "error": "feed_read URL 校验失败: ..."}
        assert server_mod._result_error(([], payload)).startswith("feed_read URL 校验失败")

    def test_a_clean_envelope_yields_nothing(self):
        assert server_mod._result_error(([], {"feeds": [], "error": ""})) == ""
        assert server_mod._result_error(([], {"feeds": []})) == ""

    def test_a_bare_dict_payload_is_read_too(self):
        assert server_mod._result_error({"error": "boom"}) == "boom"

    def test_a_list_payload_reports_the_first_error(self):
        assert server_mod._result_error([{"error": ""}, {"error": "second"}]) == "second"

    def test_a_bare_content_list_yields_nothing(self):
        assert server_mod._result_error([]) == ""

    def test_the_refusal_rides_in_the_existing_error_field(
            self, monkeypatch, tmp_path: Path):
        """端到端：`_log_tool_call` 拿到的第三个字段就是它，`select(.error)`
        能捞出每一次被工具自己判为失败的调用。"""
        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))

        server_mod._log_tool_call(
            "feed_read", True, 22.0,
            server_mod._result_error(([], {"feeds": [], "error": "refused"})))

        row = json.loads(log.read_text(encoding="utf-8").strip())
        assert row["ok"] is True          # 调用确实跑完了
        assert row["error"] == "refused"  # 而工具报告了失败


# ============================================================================
# 来源：test_the_probe_that_reads_a_filled_input_reads_the_value_the_page_holds.py
# ============================================================================
# 读一个填过的框，必须读出页面上现在那个值 —— 不是标签上那一个。
# 重名消歧：_El → _El__the_probe_that_reads_a_f_5a80、_Loc → _Loc__the_probe_that_reads_a_f_5a80、_Page → _Page__the_probe_that_reads_a_f_5a80、_run → _run__the_probe_that_reads_a_f_5a80

# ─── 一页会把 attribute 和 property 分开存的迷你 DOM ─────────────────


class _El__the_probe_that_reads_a_f_5a80:
    """`attrs` 是标签上那一层，`props` 是页面上现在那一层。

    `fill` **只动 props** —— 这一条是整个测试的物理基础。真实的 `<input>` 就是这样：
    写 `el.value = "3"` 不会重写 markup 里的 `value="1"`，所以 outerHTML 与 .value
    可以长期不一致，而只有后者会被提交上去。
    """

    def __init__(self, tag="DIV", text="", attrs=None, props=None):
        self.tag = tag.upper()
        self.text = text
        self.attrs = dict(attrs or {})
        self.props = dict(props or {})
    async def inner_text(self, timeout=None):
        return self.text

    async def get_attribute(self, attr):
        return self.attrs.get(attr)

    async def fill(self, value, timeout=None):
        self.props["value"] = value

    async def evaluate(self, js, arg=None):
        if js is _TAG_JS:
            # 交回的是**那颗 JS 实际会交回的形状**（`toLowerCase()`），不是"一个标签名"。
            # 第一版这里写的是 `return self.tag`（大写），于是 mocks 与真机在同一个字符串
            # 上给出两种答案，`_VALUEISH_TAGS` 的大小写错了整整一轮没人测到 ——
            # 靶站复验（`evals/verify_fixes.py` 的 1.1）量到的才是真相。
            return self.tag.lower()
        if js is _READ_PROPERTY_JS:
            if arg not in self.props:
                return None
            return self.props[arg]
        return None


class _Loc__the_probe_that_reads_a_f_5a80:
    def __init__(self, el):
        self._el = el

    @property
    def first(self):
        return self

    async def inner_text(self, timeout=None):
        return await self._el.inner_text()

    async def get_attribute(self, attr):
        return await self._el.get_attribute(attr)

    async def fill(self, value, timeout=None):
        await self._el.fill(value)

    async def evaluate(self, js, arg=None):
        return await self._el.evaluate(js, arg)


class _Page__the_probe_that_reads_a_f_5a80:
    def __init__(self, by_selector):
        self.els = by_selector

    def locator(self, sel):
        if sel not in self.els:
            raise RuntimeError(f"waiting for locator {sel!r}")
        return _Loc__the_probe_that_reads_a_f_5a80(self.els[sel])


def _run__the_probe_that_reads_a_f_5a80(chain, page):
    pa = build_page_action(chain)
    asyncio.run(pa(page))
    return pa.outcomes


def _filled_box():
    """markup 说 1，页面上现在放着 3 —— 就是场景 12 的那一格。"""
    return _Page__the_probe_that_reads_a_f_5a80({"#qty": _El__the_probe_that_reads_a_f_5a80(tag="INPUT", attrs={"value": "1"}, props={"value": "3"})})


# ─── 1 · 那一次 44 秒的犹豫：问 attribute，也得拿到活的值 ────────────


def test_a_filled_box_reads_the_value_the_page_holds_not_the_one_the_markup_shipped():
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "attribute": "value"}}], _filled_box())
    assert rows[0]["ok"] is True
    assert rows[0]["read"] == "3", "读回 1 就是那次'不敢发'的起点"


def test_the_receipt_says_which_layer_it_answered_from():
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "attribute": "value"}}], _filled_box())
    assert rows[0]["read_via"].startswith("property:value")


def test_the_two_layers_disagreeing_is_itself_the_evidence_the_fill_landed():
    """两数不等 → `attribute_says` 上线。这一行不需要第二次调用就回答了"填上了吗"。"""
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "attribute": "value"}}], _filled_box())
    assert rows[0]["attribute_says"] == "1"


def test_a_box_still_matching_its_markup_does_not_claim_a_difference():
    page = _Page__the_probe_that_reads_a_f_5a80({"#qty": _El__the_probe_that_reads_a_f_5a80(tag="INPUT", attrs={"value": "1"}, props={"value": "1"})})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "attribute": "value"}}], page)
    assert rows[0]["read"] == "1"
    assert "attribute_says" not in rows[0], "相等时谎报一层，是制造一条不存在的证据"


# ─── 2 · 显式那一层，与非表单域的老路 ───────────────────────────────


def test_an_explicit_property_read_answers_from_the_live_layer():
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "property": "value"}}], _filled_box())
    assert rows[0]["read"] == "3"
    assert rows[0]["read_via"] == "property:value"


def test_an_attribute_read_on_a_link_still_reads_the_markup():
    """`href` 那一族本来就没有两层可分 —— 不许为了修 input 把别的读数都换掉。"""
    page = _Page__the_probe_that_reads_a_f_5a80({"a.next": _El__the_probe_that_reads_a_f_5a80(tag="A", attrs={"href": "/p/2"})})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "a.next", "attribute": "href"}}], page)
    assert rows[0]["read"] == "/p/2"
    assert rows[0]["read_via"] == "get_attribute:href"


def test_a_textarea_and_a_select_are_treated_as_form_fields_too():
    for tag in ("TEXTAREA", "SELECT"):
        page = _Page__the_probe_that_reads_a_f_5a80({"f": _El__the_probe_that_reads_a_f_5a80(tag=tag, attrs={"value": "旧"}, props={"value": "新"})})
        rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "f", "attribute": "value"}}], page)
        assert rows[0]["read"] == "新", tag


# ─── 3 · 够不着与读错了地方：两种失败要分开说 ───────────────────────


def test_every_read_mode_names_the_layer_it_answered_from():
    """三种读法都得留出处。少一种，读的人就只能在两层之间猜自己拿到的是哪一层。"""
    page = _Page__the_probe_that_reads_a_f_5a80({"#p": _El__the_probe_that_reads_a_f_5a80(tag="P", text="正文"),
                  "a.n": _El__the_probe_that_reads_a_f_5a80(tag="A", attrs={"href": "/x"}),
                  "#q": _El__the_probe_that_reads_a_f_5a80(tag="INPUT", attrs={"value": "1"}, props={"value": "3"})})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#p"}},
                 {"read": {"selector": "a.n", "attribute": "href"}},
                 {"read": {"selector": "#q", "property": "value"}}], page)
    assert [r["read_via"] for r in rows] == ["inner_text", "get_attribute:href",
                                             "property:value"]


def test_a_property_the_element_does_not_have_fails_by_name_not_as_an_empty_read():
    """<div> 没有 value 这一层。交回 "" 会被读成"这框是空的"，那是另一种误判。"""
    page = _Page__the_probe_that_reads_a_f_5a80({"#box": _El__the_probe_that_reads_a_f_5a80(tag="DIV", text="正文", props={})})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#box", "property": "value"}}], page)
    assert rows[0]["ok"] is False
    err = rows[0]["error"]
    assert "div" in err.lower() and "form control" in err, err


def test_an_empty_form_field_says_that_is_its_current_value_not_a_missing_read():
    page = _Page__the_probe_that_reads_a_f_5a80({"#qty": _El__the_probe_that_reads_a_f_5a80(tag="INPUT", attrs={"value": ""}, props={"value": ""})})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "attribute": "value"}}], page)
    assert rows[0]["ok"] is False
    assert "current value" in rows[0]["error"], rows[0]["error"]


def test_an_empty_form_field_read_by_property_names_its_value_instead_of_crashing():
    """property 那条路漏了同一句：空值是合法读数，不是未绑定变量。"""
    page = _Page__the_probe_that_reads_a_f_5a80({"#qty": _El__the_probe_that_reads_a_f_5a80(tag="INPUT", attrs={"value": ""}, props={"value": ""})})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "property": "value"}}], page)
    assert rows[0]["ok"] is False
    err = rows[0]["error"]
    assert "empty_hint" not in err, err
    assert "current value" in err, err


# ─── 4 · 那句指路的话不许再指着错的门 ───────────────────────────────


def test_the_empty_text_read_now_points_at_the_layer_that_actually_works():
    page = _Page__the_probe_that_reads_a_f_5a80({"#challenge": _El__the_probe_that_reads_a_f_5a80(tag="P", text="   ")})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#challenge"}}], page)
    err = rows[0]["error"]
    assert '"property"' in err and "value" in err, err
    assert "screenshot" in err, "图里的挑战那一扇门不能丢"


def test_the_wrong_door_is_not_recommended_as_a_way_to_read_a_field():
    """这条是整刀的守门人：旧提示教人用 attribute 读框的值，而那正是假证据的来源。

    它回来说"这一行只是把'没读到'原样交回给人猜"—— 猜错一次的代价是 4 次调用，
    猜错的时机是不可逆写之前。
    """
    page = _Page__the_probe_that_reads_a_f_5a80({"#challenge": _El__the_probe_that_reads_a_f_5a80(tag="P", text="")})
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#challenge"}}], page)
    err = rows[0]["error"]
    assert re.search(r"an input'?s value needs \{\"attribute\"", err) is None, err


def test_the_new_hint_matches_what_the_code_will_actually_return():
    """指路句与实现必须同向：照着提示读一个填过的框，拿到的就是活的值。"""
    page = _Page__the_probe_that_reads_a_f_5a80({"#answer": _El__the_probe_that_reads_a_f_5a80(tag="INPUT", attrs={"value": ""}, props={})})
    _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#answer", "property": "value"}}], page)  # shape probe
    filled = _filled_box()
    rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "property": "value"}}], filled)
    assert rows[0]["read"] == "3"


# ─── 5 · 校验：两个来源同时给是笔误 ─────────────────────────────────


def test_asked_for_both_layers_is_refused_as_a_typo_not_a_combination():
    with pytest.raises(ValueError, match="not both"):
        _validate_actions([{"read": {"selector": "#q", "attribute": "value",
                                     "property": "value"}}])


@pytest.mark.parametrize("bad", ["href", "textContent", "", "value ", "VALUE", 7])
def test_a_property_outside_the_fillable_three_is_refused_by_name(bad):
    """value/checked/selected 之外没有"fill 会改的 property"这一说 —— 收了就是空头承诺。"""
    with pytest.raises(ValueError, match="read.property"):
        _validate_actions([{"read": {"selector": "#q", "property": bad}}])


def test_the_label_separates_the_two_layers_at_a_glance():
    assert (action_label({"read": {"selector": "#q", "attribute": "value"}})
            == "read '#q' @value")
    assert (action_label({"read": {"selector": "#q", "property": "value"}})
            == "read '#q' @value (property)")


# ─── 6 · 新键要活着走到线上，并且写在人查的地方 ─────────────────────


def test_read_via_and_attribute_says_survive_the_wire_dump():
    page = _filled_box()
    resp = ResponseModel(url="https://example.com", status=200, content=[])
    pa = build_page_action([{"read": {"selector": "#qty", "attribute": "value"}}])
    asyncio.run(pa(page))
    from dhole_mcp.quality import _report_action_outcomes
    _report_action_outcomes(resp, pa)
    row = wire._wire_dump(resp)["metadata"]["actions"][0]
    assert row["read"] == "3"
    assert row["read_via"].startswith("property:") and row["attribute_says"] == "1"


def test_the_two_layers_are_explained_in_the_volume_a_caller_reads():
    """常驻层有字数闸，所以这件事写在按需取的那一卷 —— 不在就等于只在源码里。"""
    from dhole_mcp import guide
    _header, behavior = guide.read("behavior")
    assert re.search(r"(?<![\w.\"])property(?![\w\"])", behavior)
    for needle in ("attribute", "property"):
        assert needle in behavior, needle


def test_the_new_receipt_keys_are_documented_where_a_field_is_looked_up():
    from dhole_mcp import guide
    fields = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
    for needle in ("read_via", "attribute_says"):
        assert needle in fields, needle

def test_the_tag_probe_is_case_matched_by_the_js_it_ships():
    """`_VALUEISH_TAGS` 的大小写必须由**发出去的那颗 JS** 决定，且那颗 JS 只有一个定义。

    这一条是复验（`evals/verify_fixes.py` 的 1.1）量出来的账，不是想象的守卫：第一版
    在这里写了一个大写集合，又在它上面"再定义"了一遍 `_TAG_JS`（同名、模块后被覆盖），
    于是真机上 `tag in _VALUEISH_TAGS` 恒为假 —— 问 attribute 的那一读照旧回死值，而
    离线 mock 交回的是大写标签名，测不出这个形状。两条一起钉：JS 说小写，集合就得小写；
    同一个名字不许有第二处赋值。
    """
    import inspect

    from dhole_mcp import actions as A

    assert "toLowerCase()" in A._TAG_JS, A._TAG_JS
    assert all(tag == tag.lower() for tag in A._VALUEISH_TAGS), A._VALUEISH_TAGS
    src = inspect.getsource(A)
    defs = [ln for ln in src.splitlines() if ln.startswith("_TAG_JS =")]
    assert len(defs) == 1, f"一颗 JS 有两个作者，后写的那个会静默赢下前一个：{defs}"


def test_a_form_control_is_matched_whatever_case_the_page_gives_back():
    """正向件：`<input>` 无论大小写都要走到活的那一层（判据不该由标记的写法决定）。"""
    for tag in ("INPUT", "input", "Input"):
        page = _Page__the_probe_that_reads_a_f_5a80({"#qty": _El__the_probe_that_reads_a_f_5a80(tag=tag, attrs={"value": "1"}, props={"value": "3"})})
        rows = _run__the_probe_that_reads_a_f_5a80([{"read": {"selector": "#qty", "attribute": "value"}}], page)
        row = rows[0]
        assert row["read"] == "3", (tag, row)
        assert row["read_via"].startswith("property:"), (tag, row)
