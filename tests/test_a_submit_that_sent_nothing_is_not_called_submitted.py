"""`{"submit": ...}` 没让任何东西离开这一页时，回执不许写 `submitted`。

真机（2026-10-08，本地靶站 127.0.0.1:58059 —— `allow_private` 按"用完当场收回"授权，
测完已 `cred_defaults(unset=["allow_private"])`）：

    actions = [{"fill": {"selector": "input[name=note]", "text": "…"}},
               {"submit": {"selector": "form"}}]
    → [{"action": "fill 'input[name=note]'", "ok": true},
       {"action": "submit 'form'", "ok": true, "submitted": true}]
    靶站的 posts.jsonl：不存在（一个请求都没进过这个进程）

同一条链只把选择器换成 `button[name=send]`：

    posts.jsonl = {"path": "/submit",
                   "body": "note=live-verify+D-25+button&send=yes", …}

`<FORM>` 不是"被点击就提交"的元素，click 落在它上面什么也不发生；而执行器在
`_settle_after_click` 返回之后无条件写 `shot["submitted"] = True`（click 落在提交型控件
上那一支同样）。后果不止于读者看见一句假话 —— `submitted` 正是写审计的触发键
（quality 的 browser_submit 通道读 `o.get("submitted")`），所以这一格一直在往审计里
记**根本没发生的写操作**，而那笔记录存在的意义就是"这一发真的出去了"。

判据取"这一页真的发过请求"（`_SubmissionWitness`）：document 的任何方法（`method=get`
的表单也是交表），或脚本自己发的 xhr/fetch 写请求。beacon / ping / 图片 / 埋点不算 ——
它们恰好在任何一次点击时都会发，收进证据等于把新的假阳性写死；地址栏变化也不算 ——
`history.pushState` 改 URL 而一个字节都不发。
"""
from __future__ import annotations

import pytest

from dhole_mcp.actions import build_page_action
from dhole_mcp.context import set_mcp_session
from dhole_mcp.server import (
    ResponseModel,
    _SESSION_ID,
    _report_action_outcomes,
)


class _FakeRequest:
    def __init__(self, method="POST", resource_type="document"):
        self.method = method
        self.resource_type = resource_type


class _FakePage:
    """一页 + 它的点击会发出去哪些请求（`emits`）。没有 `emits` 就一个也不发。"""

    def __init__(self, emits=(), navigate_to=""):
        self.url = "https://shop.example.com/checkout"
        self.emits = list(emits)
        self.navigate_to = navigate_to
        self._listeners: list = []
        self.attached = 0

    def on(self, event, handler):
        if event == "request":
            self._listeners.append(handler)
            self.attached += 1

    def remove_listener(self, event, handler):
        if handler in self._listeners:
            self._listeners.remove(handler)

    def sent(self) -> None:
        for method, resource_type in self.emits:
            for handler in list(self._listeners):
                handler(_FakeRequest(method, resource_type))
        if self.navigate_to:
            self.url = self.navigate_to


class _FakeElement:
    """应答两道探测：`_SUBMIT_CONTROL_JS`（看 "el.form"）与 `_TAG_JS`（看 tagName）。"""

    def __init__(self, kind=None, tag="form", name="Buy now"):
        self.kind = kind
        self.tag = tag
        self.name = name
        self.clicked: list[str] = []

    async def click(self, timeout=None):
        self.clicked.append(self.name)

    async def evaluate(self, js):
        if "el.form" in js:
            return self.kind
        if "tagName" in js:
            return self.tag
        return self.name

    async def wait_for(self, **kw):
        pass


class _FakeLocator:
    def __init__(self, el):
        self._el = el

    async def click(self, timeout=None):
        await self._el.click()

    async def evaluate(self, js):
        return await self._el.evaluate(js)

    async def wait_for(self, **kw):
        await self._el.wait_for(**kw)


async def _run(monkeypatch, actions, el=None, page=None):
    """跑一条链，返回 (page_action, 落点元素, 那一页)。

    `_settle_after_click` 是唯一被换掉的一环（点击之后的等待与观察不在这一刀的判据里），
    点击本身、witness 的挂与摘、行的成形全部走真实执行器。
    """
    el = el or _FakeElement()
    page = page if page is not None else _FakePage()

    async def fake_locator(page, target):
        return _FakeLocator(el)

    async def fake_settle(page, loc):
        await loc.click()
        page.sent()

    monkeypatch.setattr("dhole_mcp.actions._target_locator", fake_locator)
    monkeypatch.setattr("dhole_mcp.actions._settle_after_click", fake_settle)
    pa = build_page_action(actions)
    await pa(page)
    return pa, el, page


@pytest.fixture(autouse=True)
def _no_session():
    """这一刀只管证据，闸按"参数confirm"那条路走（无 elicitation 会话）。"""
    set_mcp_session(None)
    yield
    set_mcp_session(None)


def _submit(what="form", **extra):
    return [{"submit": {"selector": what, "confirm": True, **extra}}]


# ─── 1 · 什么都没发出去：不许叫 submitted ────────────────────────────────────

@pytest.mark.asyncio
async def test_a_submit_that_sent_nothing_is_not_called_submitted(monkeypatch):
    pa, el, _ = await _run(monkeypatch, _submit())
    (row,) = pa.outcomes
    assert el.clicked == ["Buy now"], "点击确实发生了，这一刀不否认它"
    assert row["ok"] is False, row
    assert "submitted" not in row, (
        f"一个请求都没离开这一页，回执却宣称提交：{row}")
    assert row.get("not_submitted") is True, row


@pytest.mark.asyncio
async def test_the_row_names_what_it_landed_on_and_both_rewrites(monkeypatch):
    """报错要指着那一棵 `<FORM>` 说，并给出两条真的能交表的路。"""
    pa, _, _ = await _run(monkeypatch, _submit())
    (row,) = pa.outcomes
    err = row["error"]
    assert "<form>" in err, err
    assert "input[type=submit]" in err, err
    assert '"press"' in err and "Enter" in err, err


@pytest.mark.asyncio
async def test_a_url_that_moved_without_a_request_is_not_evidence(monkeypatch):
    """地址栏变了不算：`history.pushState` 改 URL 而什么都不发。

    这一条钉住判据的形状 —— 证据只能是请求。放宽到"URL 不同"就把 D-29 换成了另一种
    假阳性（单页应用的路由改动会被记成一笔写操作）。
    """
    pa, _, _ = await _run(monkeypatch, _submit(),
                          page=_FakePage(navigate_to="https://shop.example.com/done"))
    (row,) = pa.outcomes
    assert row["ok"] is False and "submitted" not in row, row


# ─── 2 · 真的发出去了：证据是什么形状都认 ────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("method,rtype", [
    ("POST", "document"),   # 原生表单交表后换页
    ("GET", "document"),    # <form method=get> 也是提交
    ("POST", "xhr"),        # 单页应用：脚本自己发的写请求
    ("PATCH", "fetch"),
])
async def test_a_request_that_left_the_page_is_a_submission(monkeypatch, method,
                                                            rtype):
    pa, _, _ = await _run(monkeypatch, _submit("button[name=send]"),
                          el=_FakeElement(tag="button"),
                          page=_FakePage(emits=[(method, rtype)]))
    (row,) = pa.outcomes
    assert row["ok"] is True and row.get("submitted") is True, row
    assert row["submission"] == method, row


@pytest.mark.asyncio
async def test_a_click_on_a_submit_control_obeys_the_same_rule(monkeypatch):
    """同一个不可逆结果、同一道闸，也就同一份证据要求。"""
    ok, _, _ = await _run(
        monkeypatch, [{"click": {"selector": "button[name=send]",
                                 "confirm": True}}],
        el=_FakeElement(kind="submit", tag="button"),
        page=_FakePage(emits=[("POST", "document")]))
    (row,) = ok.outcomes
    assert row["ok"] is True and row.get("submitted") is True, row

    quiet, _, _ = await _run(
        monkeypatch, [{"click": {"selector": "button[name=send]",
                                 "confirm": True}}],
        el=_FakeElement(kind="submit", tag="button"))
    (row,) = quiet.outcomes
    assert row["ok"] is False and "submitted" not in row, row
    assert "<button>" in row["error"], row["error"]


# ─── 3 · 反向守卫：别把噪声也当证据 ──────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("method,rtype", [
    ("POST", "beacon"), ("POST", "ping"), ("GET", "image"), ("POST", "other"),
])
async def test_the_noise_a_click_always_makes_is_not_evidence(monkeypatch, method,
                                                               rtype):
    """埋点/像素就在每一次点击时发，收进证据等于把"点了"当成"交了表"。"""
    pa, _, _ = await _run(monkeypatch, _submit(),
                          page=_FakePage(emits=[(method, rtype)]))
    (row,) = pa.outcomes
    assert row["ok"] is False and "submitted" not in row, row


@pytest.mark.asyncio
async def test_a_script_that_only_reads_is_not_called_a_submission(monkeypatch):
    """xhr/fetch 只认写方法：脚本拉一份 JSON 回来不算交表。"""
    pa, _, _ = await _run(monkeypatch, _submit(),
                          page=_FakePage(emits=[("GET", "xhr")]))
    (row,) = pa.outcomes
    assert row["ok"] is False and "submitted" not in row, row


# ─── 4 · 审计里那一笔 ────────────────────────────────────────────────────────

def _browser_submits(log) -> list:
    import json as _json
    if not log.exists():
        return []
    return [r for r in (_json.loads(line) for line in
                        log.read_text(encoding="utf-8").splitlines())
            if r.get("channel") == "browser_submit"]


@pytest.mark.asyncio
async def test_no_write_is_audited_for_a_submit_that_sent_nothing(
        monkeypatch, tmp_path):
    """这一行是写审计的载荷，所以判据修的是审计本身，不是措辞。"""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))
    pa, _, _ = await _run(monkeypatch, _submit())
    result = ResponseModel(url="https://shop.example.com/checkout", status=200,
                           content=["body"], fetcher_used="stealthy")
    token = _SESSION_ID.set("auditor")
    try:
        _report_action_outcomes(result, pa)
    finally:
        _SESSION_ID.reset(token)

    writes = _browser_submits(log)
    assert writes == [], (
        f"一页没动，审计里却多了一笔写操作：{writes}"
        "（同一套 harness 记得到真提交，见下一条）")


@pytest.mark.asyncio
async def test_a_submit_that_sent_is_still_audited_once(monkeypatch, tmp_path):
    """反向：收紧判据不许把真的那一笔也漏掉 —— 审计少记一笔和多加一笔一样糟。"""
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))
    pa, _, _ = await _run(monkeypatch, _submit("button[name=send]"),
                          el=_FakeElement(tag="button"),
                          page=_FakePage(emits=[("POST", "document")]))
    result = ResponseModel(url="https://shop.example.com/checkout", status=200,
                           content=["body"], fetcher_used="stealthy")
    token = _SESSION_ID.set("auditor")
    try:
        _report_action_outcomes(result, pa)
    finally:
        _SESSION_ID.reset(token)
    writes = _browser_submits(log)
    assert len(writes) == 1 and writes[0]["pending"] is False, writes


# ─── 5 · 响应层的那一句概括 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_response_does_not_call_the_step_something_that_never_ran(
        monkeypatch):
    """一行"点了但没发出去"不许被 `next_action` 概括成"这些交互根本没发生"。

    两种失败给的出路不一样：选择器没指着东西 → 去核对选择器；指着东西而那一发没出去 →
    去换成真正会交表的那个控件。说成前者，读者就照着错的那半句去改对的那一半。
    """
    pa, _, _ = await _run(monkeypatch, _submit())
    result = ResponseModel(url="https://shop.example.com/checkout", status=200,
                           content=["body"], fetcher_used="stealthy")
    _report_action_outcomes(result, pa)
    assert pa.outcomes[0].get("not_submitted") is True
    assert "did not take effect" in result.summary, result.summary
    assert "no request left the page" in result.next_action, result.next_action
    assert "never happened" not in result.next_action, result.next_action


# ─── 6 · 听得了一次就够 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_witness_is_attached_for_a_step_and_gone_after_it(monkeypatch):
    """听一次就摘：会话可以把同一页留给下一次用，监听器不该跟着活下来。

    `attached` 让这一条两半都验到 —— 根本没挂上（等于这一刀没生效）和挂上没摘（一步
    一个泄漏）都会在这里失败。
    """
    page = _FakePage(emits=[("POST", "document")])
    pa, _, _ = await _run(monkeypatch, _submit() + _submit(), page=page)
    assert len(pa.outcomes) == 2
    assert page.attached == 2, "witness 根本没挂上过，这条什么也没验"
    assert page._listeners == [], f"监听器留在了页面上：{page._listeners}"
