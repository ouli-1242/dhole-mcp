"""Page interaction for web_read (the `actions` param).

When the agent passes `actions=[...]`, web_read forces the stealthy browser
tier and runs the actions on the page after navigation, before content
extraction. This reaches content behind a click, a search form, a "load more"
button, or infinite scroll — cases a plain fetch can't.

Implementation: patchright's stealthy fetch accepts a `page_action` callable that
receives the Playwright AsyncPage after goto and is awaited. We build that
callable from a validated list of action dicts and thread it through
web_read -> _force_fetch -> stealthy_fetch -> session.fetch(page_action=...).

Action schema (one key per dict):
  {"click": "css-selector"}                 click the first match
  {"fill": {"selector": "css", "text": "x"}}  clear + fill an input
  {"press": "Enter"}                        press a keyboard key on the page
  {"press": {"selector": "css", "key": "Enter"}}  press on a specific element
  {"wait": 500}                             wait milliseconds
  {"scroll": 3}                             3 steps of "reach the bottom, wait for
                                            the page to answer" (see _scroll_step)
  {"scroll": {"steps": 5, "selector": ".feed", "ms_per_step": 3000}}
                                            same, inside a scrollable container
  {"wait_selector": "css"}                  wait for a selector to appear
  {"wait_selector": {"selector": "css", "count": 40, "state": "visible"}}
                                            wait for N matches / a visibility state
  {"screenshot": "css"}                     crop one element to an inline image
  {"screenshot": {"selector": "css", "decode_qr": true}}
                                            same, plus local QR decode of the crop
  {"read": "css"}                           take THAT page instance's text back into
  {"read": {"selector": "css", "as": "code"}}
                                            the chain; `as` names it for a later
  {"fill": {"index": 2, "text": "answer {=code}"}}
                                            fill to answer what was just read — the
                                            one shape that works for a challenge
                                            written in the DOM (a fresh call means
                                            a fresh challenge). Optional knobs:
                                            `attribute` (read value/href instead of
                                            text), `expect`.
  {"snapshot": true}                        numbered interactive-element list
                                            (index/tag/role/name) for the agent
                                            to act on by index — no CSS needed
  {"click": {"index": 17}}                  act by INDEX (from a {"snapshot"} row);
  {"click": {"index": 17, "expect": "登录"}}  `expect` verifies the element's name
                                            before acting (a stale index must
                                            fail loudly, not click the wrong
                                            thing). `expect` also works with a
                                            selector.
  {"hover": "css-selector"}                 hover to reveal (menus, tooltips);
                                            the {"selector"|"index", "expect"?}
                                            dict form works here too
  {"drag": {"selector": "#a", "to": "#b"}}  drag one element onto another; BOTH
                                            ends take the same target shapes
                                            (a CSS selector string or
                                            {"index": N, "expect"?})
  {"select": {"selector": "#country", "option": "CN"}}
                                             choose a <select> dropdown option.
                                             `option` matches the option's value
                                             first, then its visible label; a
                                             list chooses several (multi-select
                                             only). fill cannot reach a <select>
                                             (Playwright fills inputs/textarea/
                                             contenteditable only) and click
                                             opens a native dropdown with no DOM
                                             to click inside
  {"navigate": "back"}                      history back / forward. A re-goto
                                             of the same URL gets a fresh page
                                             (form wiped, scroll reset); history
                                             returns the page the browser
                                             already holds
  {"pdf": {"save_to": "out/receipt.pdf"}}   print THIS rendered page to a PDF
                                             file - text stays selectable, so
                                             doc_parse can read it back. Same
                                             path discipline as screenshot's
                                             save_to; headless browser only
  {"submit": {"selector"|"index", "confirm"?}}
                                            the irreversible one: without
                                            confirm:true it pauses for approval.
                                            `submitted` in the receipt is earned
                                            by a request leaving that page; a
                                            target no click submits (<form>) gets
                                            `not_submitted` and audits nothing
  {"click": {..., "confirm": true}}         a click that lands on a submit
                                            control (input[type=submit|image],
                                            a form's default button) passes the
                                            SAME approval gate — the gate reads
                                            what the action does, not its name

Validation is strict (CSS selectors validated, counts/ms capped) so a bad action
fails fast instead of hanging the browser. Per-action errors are caught so one
failing step doesn't abort the rest; the agent gets the page in whatever state
it reached.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

from dhole_mcp import install as _install
from dhole_mcp.context import (
    BACKSTOP_GRACE_S, _clamp_note, _note_text, call_budget_left_s,
    out_of_band_confirm, report_progress)


logger = logging.getLogger("dhole-mcp.actions")

MAX_ACTIONS = 20
MAX_WAIT_MS = 30_000
MAX_SCROLL = 50
# Per-step and per-call waiting caps. The per-call one is the important number:
# a lazy-load feed that never stops growing must not turn into a fetch that
# never returns, so scrolling spends a bounded slice of the call budget and
# reports what it reached.
SCROLL_STEP_MS_DEFAULT = 2_000
SCROLL_STEP_MS_MAX = 10_000
SCROLL_TOTAL_MS = 20_000
# A click's own consequence gets this much time. The click action returned as
# soon as the event was dispatched, so on news.ycombinator.com a click on
# `a.more-link` extracted the OLD page and the caller read it as "the click did
# nothing" (extended report N1). Bounded, because a click on a page that ignores
# it must stay a fast no-op rather than a 4-second nap per action.
CLICK_SETTLE_MS = 4_000
CLICK_SETTLE_MIN_MS = 600
#: 一步的等待在**没人点预算**时用的那颗数（playwright 的缺省值，沿用至今的行为）。
STEP_WAIT_MS = 10_000
#: 收窄之后最少给一步留的时间。再小这一步注定超时，而那种超时与站点无关，只与
#: "我们没给时间"有关 —— 报出去是一条怪错站的谎。
STEP_MIN_MS = 1_000


def _step_deadline_ms(own_ms: int = STEP_WAIT_MS) -> int:
    """这一步最多能等多少毫秒：自己的预算与**这次调用剩下的**取小。

    为什么不是"整个余额"：交账要时间。`_within_call_budget` 的兜底在
    `asked + BACKSTOP_GRACE_S` 上掐，实测一次 `timeout=60000` 的调用里，一步 10 秒的
    等待把整条链带过那条线，回来的是 61,529ms 加 0 字节（体验模拟报告场景 10，靶站
    `/captcha`）。预算之外那一段由兜底结，预算之内的每一步都得自己结 —— 这一步收窄
    之后，链是在余额里停下来的，信封与已有的回执因此还交得出去。

    不在预算作用域里（CLI、直连 `dispatch`、单测）时原样交回 `own_ms`：那颗数今天就
    是行为，动它等于改所有没点名预算的调用。余额已经不够一步时同样交回原值 ——
    让兜底说那句"预算用完"（真话），而不是造一个 1 毫秒的等待冒充站点的失败。
    """
    left = call_budget_left_s()
    if left is None:
        return own_ms
    cap_ms = int(left * 1000) - int(BACKSTOP_GRACE_S * 1000)
    if cap_ms < STEP_MIN_MS:
        return own_ms
    return min(own_ms, cap_ms)

_VALID_KEYS = {"click", "fill", "press", "wait", "scroll", "wait_selector",
               "screenshot", "snapshot", "submit", "hover", "drag", "read",
               "upload", "dialog", "tab", "select", "navigate", "pdf"}

#: tab 动作的帽。页签是浏览器进程里的真实页面，一枚上下文开几十个是自找内存病；
#: 50 个对"填一半、查资料、回来提交"绰绰有余。
MAX_TABS = 50
_TAB_VERBS = ("new", "close", "list")

#: navigate 的两个方向。历史导航是页自己的事（不新开页签）：back/forward 交回的
#: 是浏览器已经存着的那一页 —— 填了一半的表单、滚过的位置、SPA 的内存状态都在；
#: 重新 goto 同一个 URL 拿到的是一张新页面。playwright-mcp 的 navigate_back 同款。
_NAVIGATE_VERBS = ("back", "forward")

# upload 的两个帽。文件是**读**进浏览器的那一侧，所以路径按读侧纪律走（realpath
# 展开链接后过系统目录名单）；大小帽是给浏览器进程留的余量——set_input_files 会把
# 文件字节装进浏览器，一枚 4GB 的 ISO 装进去是把自己打挂。
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_FILE_BYTES = 50_000_000

#: dialog 的两种答案。缺省（没武装）时页面上弹出的对话框按 Playwright 的默认
#: 处置：自动 dismiss——安全侧正确（一个 confirm 永远不该在没人点头时被 accept），
#: 但它**静默**：agent 点了"删除"却什么都没发生，回执里一个字都没有。账本式监听
#: 器把这层哑巴亏补上（见 build_page_action 里那段）。
_DIALOG_ANSWERS = ("accept", "dismiss")


def _target_payload_keys() -> set[str]:
    """`_act_target` 那一支共用的寻址键，同样从它自己的正文里挖。"""
    import ast
    import inspect

    found: set[str] = set()
    for sub in ast.walk(ast.parse(inspect.getsource(_act_target))):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "val"
                and sub.args and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)):
            found.add(sub.args[0].value)
    return found


def action_payload_keys() -> dict[str, set[str]]:
    """每一颗动作**看得见**的载荷键，从 `_validate_actions` 的正文推出来。

    为什么不是另一张表：同一张形状表此前有三份作者 —— 校验器的正文（唯一真的那份）、
    常驻层那句 `fill needs text, press needs key, drag needs to`，以及 `tests` 里手抄的
    一份 `PAYLOAD_BY_KEY`。手抄的那份每加一颗动作就得跟着改，而忘了改的后果不是测试红，
    是测试**照绿**：一张不再等于行为的形状清单，正是这一族"声明里没写、只能先失败一次
    才知道"的老洞（`/hoverdrag` 那一次量到的三条 0 秒拒参、约 90 秒白烧）。
    现在表跟着正文走，唯一的作者就是读键的那一步。

    读法只有三种，都是这份函数体自己的写法：`val["k"]`、`val.get("k")` /
    `spec.pop("k")`、以及 `"k" in val`；再加 `_act_target` 那一支共用的寻址键
    （`selector` / `index` / `expect`，只给真的调用它的动作 —— `press` 自己读这两颗，
    `scroll` 只读 `selector`，把三者一律发给每颗动作就是又一张贴在行为上的标签）。
    """
    import ast
    import inspect

    payload_bases = ("val", "spec")

    def walked(body):
        """一支分支的语句表摊成节点流（`ast.walk` 只吃节点，不吃列表）。"""
        for stmt in body:
            yield from ast.walk(stmt)

    def reads(body) -> set[str]:
        found: set[str] = set()
        for sub in walked(body):
            if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                    and sub.value.id in payload_bases
                    and isinstance(sub.slice, ast.Constant)
                    and isinstance(sub.slice.value, str)):
                found.add(sub.slice.value)
            elif (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr in ("get", "pop")
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id in payload_bases
                    and sub.args and isinstance(sub.args[0], ast.Constant)
                    and isinstance(sub.args[0].value, str)):
                found.add(sub.args[0].value)
            elif (isinstance(sub, ast.Compare)
                    and isinstance(sub.ops[0], (ast.In, ast.NotIn))
                    and isinstance(sub.comparators[0], ast.Name)
                    and sub.comparators[0].id in payload_bases
                    and isinstance(sub.left, ast.Constant)
                    and isinstance(sub.left.value, str)):
                found.add(sub.left.value)
        return found

    def calls_target(body) -> bool:
        return any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                   and sub.func.id == "_act_target"
                   for stmt in body for sub in ast.walk(stmt))

    def branch_of(node) -> str:
        """`if key == "X"` 的那颗 X，不是这一形就返回空串。"""
        test = node.test
        if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "key" and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.comparators[0], ast.Constant)
                and isinstance(test.comparators[0].value, str)):
            return ""
        name = test.comparators[0].value
        return name if name in out else ""

    tree = ast.parse(inspect.getsource(_validate_actions))
    out: dict[str, set[str]] = {k: set() for k in _VALID_KEYS}
    addressing = _target_payload_keys()
    # `ast.walk` 而不是顺着 orelse 走：那条 if/elif 链套在 `for i, a in
    # enumerate(actions)` 的循环体里，顺着 If 的 else 支爬会在循环那里断掉
    # （第一版就是这么量的到一张全空的表 —— 一张全空的表看起来像"这些动作没有载荷键"，
    # 而那正是这一刀要消灭的那句话）。
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        name = branch_of(node)
        if not name:
            continue
        keys = reads(node.body)
        if calls_target(node.body):
            keys |= addressing
        out[name] |= keys
    return out

# 链内读（A3-b）的三份边界。字符帽沿用回执那一族的口径：读回来的文本进
# `metadata.actions`，而那是每一条响应都要带的行 —— 一整个 `<pre>` 的正文塞进
# 回执，代价由每一个读到这行的会话付。截断必须点名（钳必须点名）：帽在几、原文
# 共几，两个数都在行里，读的人不必猜那一枚验证码是不是被砍了一半。
MAX_ACTION_READ_CHARS = 2_000
# `fill.text` 的帽：验证期量的是发来的字面量，展开 `{=名字}` 之后还要再量一次
# （见 _substitute_refs）—— 一次读回来的正文可能比帽长，那不该变成一次静默截断
# 的填表，也不该变成一发给网站的半截答案。
MAX_FILL_TEXT_CHARS = 5_000
# `{=名字}` 是"把这一条链里更早那次 read 的结果填在这里"的记号。刻意不用裸
# `{名字}`：填进表单的文本里出现 `{code}` 这种字面量是常事（模板、JSON、占位
# 说明），把那种东西判成引用就等于悄悄改了调用方的字节。`=` 不是合法标识符的
# 首字符，所以这个记号在真实文本里出现的概率接近零，而它一旦被写成没登记过的
# 名字，是**具名拒绝**而不是原样填进去 —— 静默把 `{=cod}` 交给网站，填进去的就是
# 那六个字符。
_REF_RE = re.compile(r"\{=([a-z_][a-z0-9_]{0,30})\}")
_AS_RE = re.compile(r"[a-z_][a-z0-9_]{0,30}")
# HTML 属性名：`value` / `href` / `data-token` / `xml:lang` 都在这一形里。
_ATTR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,40}")
# `read.property` 收的那三个名字 —— 它们在这一族元素上**只以 DOM property 存在**，
# 标签上对应的那个 attribute 从来不会跟着动。见 _READ_PROPERTY_JS 上面那段。
_READ_PROPERTY_RE = re.compile(r"(value|checked|selected)")

# 表单域上的 value/checked/selected 是 property：fill 改的是 property，标签上的
# attribute 一个字都不动。拿 `get_attribute("value")` 去验证自己刚填进去的东西，
# 交回的是**一枚看起来像证据的旧数** —— 实测过的那一例：靶站 `/checkout` 上
# `fill '#qty'` 回执 `ok:true`、真正 POST 出去的也确实是 3（服务端 orders.log 为证），
# 而 `read{attribute:"value"}` 报回 "1"。那一读是按下不可逆提交之前唯一的回读手段，
# 于是它把一次正确的操作说成了一次失败的，代价是 4 次复验调用约 44 秒的"不敢发"。
#
# 这一族曾经还有一处自相矛盾的证据在同一文件里：`read` 读标签那一层，而 fill 改的是
# property 那一层，同一个页面两套口径，人只能挑一个信。这里统一到活的那一侧 ——
# 现在答 property 的就是 `_READ_PROPERTY_JS` 与 `_NAME_LADDER_JS` 上方那句 `expect`
# 说明里划清的那两扇门。
# 名字那一族（snapshot / expect）**不跟着走活层**，且是有意的：它要的是元素的稳定
# 身份，而字段此刻打着什么字会随输入变；跟着走就等于把用户数据抄进上下文
# （`e2e_the_snapshot_name_and_the_expect_check_name_the_same_thing.py` 钉着这一条）。
_VALUEISH_ATTRS = frozenset({"value", "checked", "selected"})
# 大小写跟着**发出去的那颗 JS** 走：本文件唯一一份 `_TAG_JS`（下面 snapshot 那一节
# 上方）返回的是 `toLowerCase()`，所以这里必须是小写。这条不是风格：第一版把它写成
# 大写集合，而我在它上面又"定义"了一遍 `_TAG_JS`（同名、后被覆盖），于是真机上
# `tag in _VALUEISH_TAGS` 恒为假 —— 靶站复验量到的原形状就是它：`fill` 之后
# `read{attribute:"value"}` 照旧回死值 "1"，改的只有 `property` 那一读。集合小写 +
# 一名一个定义，两条都由 `test_the_tag_probe_is_case_matched_by_the_js_it_ships` 钉住。
_VALUEISH_TAGS = frozenset({"input", "textarea", "select"})
# 返回 null 表示"这个元素没有这一层"，调用处把它说成一次具名失败而不是空串 ——
# 把"够不着"读成"值是空的"，正是这一族最贵的误读（一张空的验证码框和一个不存在
# value 属性的 <div>，在回执里长得一模一样）。
_READ_PROPERTY_JS = """(el, name) => {
  if (name === 'value') return ('value' in el) ? el.value : null;
  if (name === 'checked') return ('checked' in el) ? !!el.checked : null;
  if (name === 'selected') return ('selected' in el) ? !!el.selected : null;
  return (el[name] === undefined || el[name] === null) ? null : el[name];
}"""

# select 的解析在页面那一侧一次做完：每一颗想选的 option 先按 value 匹配、再按
# 可见标签匹配（快照行里那列名字是标签，而 value 常是 "CN"/"+86" 这类机器码，
# 只认一边就等于让调用方先猜页面用的是哪一边）。match 失败的那颗回 null，连同
# 这个 <select> 自己的 multiple 位与一份有界的选项清单一起交回 —— 失败要说清
# "这里有哪些可以选"，而不是让调用方再发一次 read 去数。
_SELECT_RESOLVE_JS = """(el, wants) => {
  const opts = Array.from(el.querySelectorAll('option'));
  const labelOf = (o) => (o.label || o.text || '').trim();
  const pick = (w) => {
    let hit = opts.find(o => o.value === w);
    if (!hit) hit = opts.find(o => labelOf(o) === w);
    return hit ? [hit.value, labelOf(hit)] : null;
  };
  return {tag: (el.tagName || '').toLowerCase(), multiple: !!el.multiple,
          resolved: wants.map(pick),
          options: opts.slice(0, 12).map(o => [labelOf(o), o.value])};
}"""

# An element capture only exists to be LOOKED at. 17.0: always available ——
# the recognition happens agent-side (dhole ships no vision model), and a
# text-only caller simply never asks for {"screenshot"}.
# Element captures ride the same page instance as the surrounding fill/click
# actions — that is the whole point over a separate screenshot call: the crop is
# taken of the page THIS chain navigated, before anything else replaces it.
# It is not "the captcha the next action answers": a capture reaches the caller
# only after the chain has run, so no step of that chain can type what it shows.
# Answering inside one navigation is what {"read"} + {"fill": "{=name}"} is for
# (text the page holds); an image challenge has no in-chain answer at all —
# that case is a hand-off, and this comment used to claim the capture was the
# way around it.
# A capture this large is a mistake (the whole page, not a cropped element), and
# shipping it spends a multimodal client's context.
MAX_ACTION_SHOT_BYTES = 2_000_000

# ─── 捕获内核（A1：自 server.py 的 web_screenshot 方法搬入）───────────────────
# web_screenshot 独立工具下线后，这五个 helper 的唯一消费者是动作链的 screenshot
# 执行器 —— 它们住在动作的家。错误映射表原样随迁：它按 headline 子串匹配，顺序
# 即优先级（窄信号在前），test_v17_fix_plan 钉着这个约定。

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_BROWSER_ERROR_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    # Must come first: it is the narrow signal. A "matched no element" headline
    # must not be read as a rendering timeout, which is the whole defect this
    # entry exists for.
    (("matched no element",),
     "the selector matched nothing on the loaded page - check it against the real "
     "DOM (extraction_type='html', or screenshot the full page first); use "
     "wait_selector only if the element appears later"),
    # Before the timeout row, and for a reason of the same kind as that row's own:
    # the driver says "Timed out while launching browser" when the OS refuses to
    # create the process, and "raise timeout=" is advice for a slow page on a
    # machine whose browser cannot start at all.
    (("cannot access executable", "access is denied", "拒绝访问", "failed to launch",
      "while launching browser", "(0x5)"),
     "the browser binary is on disk but the operating system refused to start it - "
      "no page-level parameter changes that; `dhole --doctor` names the state, and "
      "web_read(url, cdp_url='http://127.0.0.1:9222', confirm=true) uses a browser "
      "you started yourself"),
    (("timeout", "exceeded"),
     "the page never finished rendering - raise timeout= (ms), or wait for "
     "a specific element with wait_selector="),
    (("has been closed", "target closed"),
     "the browser session is gone - call again to get a fresh one, or forget it "
     "with sys_state(clear=['<session id>']) and retry"),
    (("executable doesn't exist", "playwright install", "browser_binary_missing"),
     "the browser binary is missing - dhole installs it on first use, so this means "
     f"that install failed or is still running: `{_install.browser_binary_command()}` "
     "(state: ~/.dhole/browser_install.json)"),
)


async def _locator_or_raise(page, selector: str):
    """The locator for `selector`, or LookupError when nothing on the page matches.

    Split out of the screenshot capture so the rule it enforces can be tested
    without a browser: `.first` on a locator that will never match waits the
    whole call budget out (30s by default) and then reports a TIMEOUT, which
    the error mapping reads as "the page never finished rendering" - advice
    (raise `timeout`, add `wait_selector`) that cannot conjure an element that
    is not in the DOM. Measured: a selector matching nothing and a page that
    genuinely never rendered produced the same sentence.
    """
    loc = page.locator(selector)
    if await loc.count() == 0:
        raise LookupError(
            f"selector matched no element on the loaded page: {selector!r}")
    return loc


def _resolve_save_to(save_to: str, image_type: str) -> Path:
    """Validate a save_to path BEFORE a browser is started, and return it.

    Split from the write so a typo costs an instant refusal instead of a 10-second
    cold start followed by "could not write".
    """
    raw = (save_to or "").strip()
    if not raw:
        raise ValueError(
            "save_to needs a path to write the image to (e.g. "
            "'C:/shots/page.png' or './page.png')")
    target = Path(raw).expanduser()
    if target.is_dir():
        raise ValueError(f"save_to {str(target)!r} is a directory, not a file")
    if target.suffix.lower() not in _IMAGE_SUFFIXES:
        # 后缀校验不以「文件已存在」为前提（G25 评审）：往任意新路径写字节是
        # save_to 唯一的写原语，名字不像图片就不放行——描述里说的正是这个，
        # 一个尚不存在的 notes.txt / task.bat 与一个既有的同等对待。
        if target.exists():
            raise ValueError(
                f"refusing to overwrite {str(target)!r} with image bytes - it is not "
                f"named like an image; pass a path ending in .{image_type.lower()}")
        raise ValueError(
            f"refusing to create {str(target)!r} - it is not named like an image; "
            f"pass a path ending in .{image_type.lower()}")
    # 读侧的房间黑名单对写侧同样生效：System32、~/.ssh、~/.dhole 不因为
    # 「后缀像图片」就变成可写的目的地。realpath 先展开链接，与读侧共用
    # 同一份清单 —— 就地 import：模块级 import server 会绕成循环。
    from dhole_mcp.server import _blocked_path_prefix
    blocked = _blocked_path_prefix(os.path.realpath(target))
    if blocked:
        raise ValueError(
            f"refusing to write {str(target)!r} - it is in a restricted "
            f"system directory ({blocked})")
    return target


def _resolve_pdf_save_to(save_to: str) -> Path:
    """`pdf` 动作的 save_to：`_resolve_save_to` 的姊妹，同一套纪律、后缀名单换成 .pdf。

    为什么不共用一个函数：那份的错误话术是图片的（"not named like an image"），
    参数也是 image_type；PDF 有自己的话要说（"not named like a PDF"）。两条门各
    说各的，比一个带 kind 参数、两种话术都藏着一半的门诚实。落盘复用
    `_write_screenshot`（同一颗写原语，写坏报一句具名的 OSError）。
    """
    raw = (save_to or "").strip()
    if not raw:
        raise ValueError(
            "pdf needs a save_to path to write the file to (e.g. "
            "'C:/out/receipt.pdf' or './receipt.pdf')")
    target = Path(raw).expanduser()
    if target.is_dir():
        raise ValueError(f"pdf save_to {str(target)!r} is a directory, not a file")
    if target.suffix.lower() != ".pdf":
        raise ValueError(
            f"refusing to create {str(target)!r} - it is not named like a PDF; "
            "pass a path ending in .pdf")
    from dhole_mcp.server import _blocked_path_prefix
    blocked = _blocked_path_prefix(os.path.realpath(target))
    if blocked:
        raise ValueError(
            f"refusing to write {str(target)!r} - it is in a restricted "
            f"system directory ({blocked})")
    return target


def _resolve_upload_files(files, where: str) -> list[str]:
    """Validate the upload file list BEFORE a browser starts; return real paths.

    upload 把本机文件交给页面——它是这个工具面里第一个「读本机」的动作，路径
    纪律按**读侧**走：realpath 先展开符号链接（/tmp 里一枚指向 ~/.ssh 的链接
    不能绕过名单），再过 `_blocked_path_prefix` 的系统目录与凭据清单。写侧
    （save_to）管的是"往哪里写"，这里管的是"从哪里读"——同一个名单，两个方向。

    大小帽在交浏览器之前也拦一道：set_input_files 会把文件字节装进浏览器进程，
    一枚巨大的文件是把自己打挂，拒绝要发生在浏览器还没起来的时候。
    """
    if not isinstance(files, list) or not files:
        raise ValueError(
            "action 'upload' must be {selector|index, files} - a list of "
            f"1..{MAX_UPLOAD_FILES} file paths (e.g. "
            '{"upload": {"selector": "input[type=file]", '
            '"files": ["./report.pdf"]}})')
    if len(files) > MAX_UPLOAD_FILES:
        raise ValueError(
            f"{where} lists {len(files)} files - the cap is {MAX_UPLOAD_FILES}")
    out: list[str] = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"{where}.files must be non-empty path strings, got {raw!r}")
        target = Path(raw).expanduser()
        if not target.exists():
            raise ValueError(
                f"{where}: file not found: {raw!r} - the path is resolved on "
                "this machine, before the browser runs")
        if target.is_dir():
            raise ValueError(f"{where}: {raw!r} is a directory, not a file")
        real = os.path.realpath(target)
        from dhole_mcp.server import _blocked_path_prefix
        blocked = _blocked_path_prefix(real)
        if blocked:
            raise ValueError(
                f"refusing to upload {raw!r} - it is in a restricted system "
                f"directory ({blocked})")
        size = target.stat().st_size
        if size > MAX_UPLOAD_FILE_BYTES:
            raise ValueError(
                f"{where}: {raw!r} is {size:,} bytes (cap "
                f"{MAX_UPLOAD_FILE_BYTES:,}) - the bytes load into the browser "
                "process, so a huge file is refused rather than loaded")
        out.append(real)
    return out


def _write_screenshot(data: bytes, target: Path) -> str:
    """Write captured bytes to a validated path; return the absolute path.

    Why this exists at all (G10): the image itself only reaches a multimodal
    client. Every other model got "image unavailable" plus a pile of base64 it
    cannot read, so the screenshot was effectively not a feature — a path is the
    form that survives into a text-only transcript and out to another tool.
    """
    try:
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as e:
        raise ValueError(f"could not write {str(target)!r}: {str(e)[:160]}") from None
    return str(target.resolve())


def _screenshot_error_message(exc: BaseException) -> str:
    """Turn a browser-capture exception into one actionable line for the agent.

    patchright/playwright raise multi-line dumps: a one-line headline followed
    by a "Call log:" block with per-step indentation ("waiting for fonts to
    load"). That block describes the *driver's* internals, not the caller's
    problem - it names no argument the agent could change, so shipping it spends
    context and buries the one line that does matter. Keep the exception type
    and the headline, drop the log, and append the move that usually fixes it.
    The raw exception is not lost: the caller logs it for the operator.
    """
    text = str(exc).strip()
    headline = text.splitlines()[0].strip() if text else ""
    if not headline:
        headline = "(no message)"
    flat = " ".join(headline.split())
    if len(flat) > 160:
        flat = flat[:157].rstrip() + "..."
    low = flat.lower()
    hint = ("call again; if it persists, open the URL in a browser to check it "
            "renders without a challenge")
    for needles, candidate in _BROWSER_ERROR_HINTS:
        if any(n in low for n in needles):
            hint = candidate
            break
    return f"{type(exc).__name__}: {flat} - {hint}"

# ─── MVP-2：元素索引快照与索引化动作 ────────────────────────────────────────
# "用户不用懂"由外层 agent 的语言能力提供，不由 dhole 的模型提供（计划第八章）：
# {"snapshot": true} 把可交互元素编号列给 agent 看，动作参数用 {"click":
# {"index": N}} 寻址——dhole 全程零模型零密钥。
#
# 快照与 act 必须共享同一个枚举口径，索引才可复现：两边都用这一个选择器的
# 文档序匹配列表（snapshot 用 querySelectorAll，act 用 Playwright locator.nth，
# 两者对同一选择器给出同一顺序）。宁可把 hidden 元素也编进去（编号跨两次
# 页面加载稳定），也不做可见性过滤（那会让编号依赖渲染时机）。
INTERACTIVE_SELECTOR = (
    "a, button, input, select, textarea, "
    "[role=button], [role=link], [role=tab], [role=checkbox], [role=radio], "
    "[role=menuitem], [role=combobox], [role=option], [role=switch], "
    "[role=textbox], [onclick], [tabindex]"
)

# 全页 a11y 树动辄数十 KB token。快照只回可交互元素、设数量上限，超过就如实
# 说截断（照 Playwright MCP 的做法），否则 L2 上线第一天就爆上下文。
SNAPSHOT_CAP = 80
MAX_ELEMENT_INDEX = 5000

# ─── 元素名字怎么取，只在这里写一遍 ───────────────────────────────────────────
# 两位读者：`{"snapshot": true}` 列出的那一列（_ENUM_JS），和 `expect` 核对用的那一个
# （_NAME_JS，`_check_expect` 与 `_target_locator` 各取一次）。`references/tools.md:29`
# 把两者都说成 "the element's name / visible name"，所以它们**必须**是同一个字符串：
# 列表里写着"手机号"，`expect:"手机号"` 就得认。从前是两份各写一遍的 JS，于是
# snapshot 那一侧刚堵住 value（`e2e_a_snapshot_does_not_name_an_element_by_its_contents
# .py`），expect 这一侧还留着 `el.value` —— 同一元素两套名字，正确写法当场失败，而失败
# 那句 `element 2 reads 'hunter2secret'` 把密码框此刻的字抄进了上下文（真 chromium 实测，
# 见 `e2e_the_snapshot_name_and_the_expect_check_name_the_same_thing.py`）。
#
# `value` 在哪一类元素上**就是**它的标签，在哪一类元素上**是用户的数据**：
# `input[type=submit]` 的 value 是按钮上写着的那两个字，而 tel / text / hidden /
# password 的 value 是手机号、身份证、会话 token。不区分这两种的写法，一次快照或一次
# `expect` 失配就把整张表最敏感的一列原样交出去。取值的能力没有收走 —— 它在那扇写明了
# 的门后面：{"read": {"selector": "#phone", "property": "value"}}。
_NAME_LADDER_JS = """(el) => {
  const tag = el.tagName.toLowerCase();
  const itype = tag === 'input' ? String(el.type || 'text').toLowerCase() : '';
  const VALUE_IS_LABEL = tag === 'input'
      && (itype === 'submit' || itype === 'button' || itype === 'reset');
  // textarea 的 innerText 就是它的内容，所以它和 input 一样算"装着数据"。
  const HOLDS_DATA = (tag === 'input' && !VALUE_IS_LABEL
                      && itype !== 'checkbox' && itype !== 'radio'
                      && itype !== 'file') || tag === 'textarea';
  let name = el.getAttribute('aria-label') || '';
  if (!name && !HOLDS_DATA) name = (el.innerText || '').trim();
  // 关联的 <label> 比 name 属性更像人会说出口的那句话（"手机号" 而不是 "phone"）。
  if (!name && el.labels && el.labels.length)
    name = (el.labels[0].innerText || '').trim();
  if (!name && VALUE_IS_LABEL && el.value !== undefined && el.value !== null)
    name = String(el.value);
  if (!name) name = el.placeholder || el.title || el.alt || el.name
                    || el.id || '';
  return String(name).trim();
}"""

_ENUM_JS = """(sel) => {
  const nameOf = """ + _NAME_LADDER_JS + """;
  const els = document.querySelectorAll(sel);
  const out = [];
  for (let i = 0; i < els.length && i < """ + str(MAX_ELEMENT_INDEX) + """; i++) {
    const el = els[i];
    const tag = el.tagName.toLowerCase();
    const itype = tag === 'input' ? String(el.type || 'text').toLowerCase() : '';
    let role = el.getAttribute('role') || '';
    if (!role) {
      const implicit = {a: 'link', button: 'button', select: 'combobox',
                        textarea: 'textbox', option: 'option', input: 'textbox'};
      role = implicit[tag] || '';
      if (tag === 'input') {
        const byType = {checkbox: 'checkbox', radio: 'radio', button: 'button',
                        submit: 'button', file: 'button', range: 'slider',
                        number: 'spinbutton', search: 'searchbox'};
        role = byType[itype] || 'textbox';
      }
    }
    out.push([i, tag, role, nameOf(el).slice(0, 80),
              !!el.disabled || el.getAttribute('aria-disabled') === 'true',
              tag === 'input' ? itype : '']);
  }
  return [out, els.length];
}"""

# act 时核对元素名（`expect` 的实现）：索引在两次页面加载之间可能指到不同的元素——
# 核对名字让"过期的索引"大声失败，而不是点到别的东西。它**用的就是上面那一份阶梯**，
# 因为"编号处现在写着什么"必须与快照列出的那一列逐字相等，否则快照之后按编号操作的
# 标准写法（expect 复述列表里那个名字）会在填过的字段上永远失配。
# `expect` 要的是元素的**稳定身份**：字段此刻打着什么字会随输入变，不配当身份；要验
# "填进去的字此刻在页面上"走的是另一扇门（`read` 的 property:"value" 与 `attribute_says`，
# 见 references/fields.md:147）。
_NAME_JS = _NAME_LADDER_JS

_COUNT_JS_INTERACTIVE = "(sel) => document.querySelectorAll(sel).length"

# 提交型控件识别（评审R1刀1）：click 的闸只拦"这一下真的会发表单"的控件。
# input 只认显式 type=submit|image（缺省 type 的 input 是 text，从不提交）；
# button 的缺省 type 就是 submit，但 button/reset 类型与没有 form 关联的
# 都够不着任何表单；role=link 是"长得像按钮的链接"——链接导航，不提交。
_SUBMIT_CONTROL_JS = """el => {
  const tag = (el.tagName || '').toLowerCase();
  if (tag !== 'input' && tag !== 'button') return null;
  if ((el.getAttribute('role') || '').toLowerCase() === 'link') return null;
  const type = (el.getAttribute('type') || '').toLowerCase();
  if (tag === 'button' && (type === 'button' || type === 'reset')) return null;
  if (tag === 'input' && type !== 'submit' && type !== 'image') return null;
  return el.form ? 'submit' : null;
}"""

# 那一发没出去时，回执要点名它落在什么元素上（<FORM> 不会被"点击"提交）。
_TAG_JS = "el => (el.tagName || '').toLowerCase()"


async def snapshot_elements(page) -> tuple:
    """枚举当前页面的可交互元素。返回 (rows, total, truncated)。

    rows 是 [index, tag, role, name, disabled, input_type] 的列表（截断到
    SNAPSHOT_CAP），total 是页面上的全量数——截断要如实说，不能让 agent 以为
    看到了全部。

    子 iframe 的可交互元素跟在主框后面，全局编号连续，每行多一格 `frame`（帧的
    URL）；主框行的第七格不存在——老消费者按 len>=6 读，形状照旧。索引 → 帧 的
    映射记在 `page._dhole_frame_map`（快照是它唯一的作者，`resolve_index` 是唯一
    的读者），于是快照行号对 iframe 内的元素照样可用——「用户不用懂」的合同
    不在框边上破。
    """
    try:
        got = await page.evaluate(_ENUM_JS, INTERACTIVE_SELECTOR)
    except Exception:
        got = []
    rows, total = (got if isinstance(got, list) and len(got) == 2 else ([], 0))
    if not isinstance(rows, list):
        rows, total = [], 0
    all_rows: list = list(rows)
    frame_map: dict[int, tuple] = {}
    frame_total = 0
    try:
        children = [f for f in page.frames if f is not page.main_frame]
    except Exception:
        children = []
    offset = len(all_rows)
    for fr in children:
        url = fr.url or ""
        if not url or url == "about:blank":
            continue
        try:
            fg = await fr.evaluate(_ENUM_JS, INTERACTIVE_SELECTOR)
        except Exception:
            continue
        frows, ftotal = (fg if isinstance(fg, list) and len(fg) == 2
                         else ([], 0))
        ftotal = int(ftotal or 0) if isinstance(frows, list) else 0
        if not ftotal:
            continue
        for local, r in enumerate(frows):
            if offset >= MAX_ELEMENT_INDEX:
                break
            if not (isinstance(r, list) and len(r) >= 6):
                continue
            all_rows.append([offset, r[1], r[2], r[3], r[4], r[5], url])
            frame_map[offset] = (fr, local)
            offset += 1
        frame_total += ftotal
    if frame_map:
        try:
            page._dhole_frame_map = frame_map
        except Exception:
            pass
    shown = all_rows[:SNAPSHOT_CAP]
    # 截断在截取之后判定：JS 侧的上限是 MAX_ELEMENT_INDEX（防失控页），这里的
    # 上限是 SNAPSHOT_CAP（防上下文爆炸）——total 超过"实际展示数"才是截断。
    truncated = (int(total or 0) + frame_total) > len(shown)
    return shown, int(total or 0) + frame_total, truncated


async def resolve_index(page, index: int):
    """索引 → Playwright locator（与快照同一选择器、同一文档序）。

    快照枚举过的子 iframe 行（`page._dhole_frame_map`，snapshot_elements 写）
    在这里认领自己的帧；没枚举过的帧内元素不存在索引——主框的行号从不说谎。
    """
    fmap = getattr(page, "_dhole_frame_map", None)
    if isinstance(fmap, dict) and index in fmap:
        fr, local = fmap[index]
        return fr.locator(INTERACTIVE_SELECTOR).nth(local)
    try:
        count = await page.evaluate(_COUNT_JS_INTERACTIVE, INTERACTIVE_SELECTOR)
    except Exception:
        count = None
    if isinstance(count, int) and index >= count:
        raise ValueError(
            f"element index {index} is beyond this page's {count} interactive "
            "elements - the page changed since the snapshot; re-run the "
            '{"snapshot": true} action')
    return page.locator(INTERACTIVE_SELECTOR).nth(index)


async def _check_expect(page, index: int, expect: str) -> None:
    """`expect`：核对索引处元素的名字。失败信息里带着现在的名字，agent 能
    直接判断"页面变了，重新快照"。"""
    loc = await resolve_index(page, index)
    try:
        name = await loc.evaluate(_NAME_JS)
    except Exception as e:
        raise ValueError(
            f"could not read element {index} to verify it ({type(e).__name__}) - "
            "the page may have changed since the snapshot") from e
    if expect.strip().lower() not in str(name).strip().lower():
        raise ValueError(
            f"element {index} reads {str(name)[:80]!r}, expected ~{expect!r} - "
            "the page changed since the snapshot; re-run {\"snapshot\": true} "
            "and act on the fresh indices")


def _screenshot_shape_error(i: int) -> ValueError:
    """一颗参数一条规则一句报错（P2-8）。

    校验器接受的形状就五种：selector 串、裸行号（刀9-6c · F8）、`{"selector": ...}`、
    `{"index": N}`、`{"full_page": true}`。落在这五种之外的（bare `true`、没有目标的
    dict）念的是**同一句**，句里列全五种 —— 评审现场是三条各自说法：bare true 那句漏了
    full_page（而 full_page 恰恰是当时唯一能成功的"无目标"写法），无目标 dict 那句
    借用了 click/fill 的措辞、不提 full_page，于是纯整页截图在语法上读不出来。
    花括号伪影也一并清掉：`{{...}}` 是字符串拼接漏进响应里的 Python 语法，不是
    调用方该读的 JSON 形状。

    裸 `{"screenshot": true}` 的语义已有判决（2026-10-08，operator）：**维持拒绝**。
    两条改判都量过 —— 当整页：实测 rfc9110 的整页捕获在内部 10s 上限里就超时失败，
    短页能出图而那正被 `MAX_ACTION_SHOT_BYTES` 的注释称作弄错了；当视口：那是新能力，
    要在描述里加字（连接面只降不升），还让"没有目标"有两种意思。
    病根不在判决而在**相邻两扇门同一副语法**：`{"snapshot": true}` 收裸布尔。所以这句
    话除了说"你写错了"，还要点名那扇隔壁的门（给一份可照抄的写法），并说一句为什么
    这里收不下裸布尔 —— 拍照总得有"拍什么"。裸**整数**不受这条判决影响：它有明确的
    "拍什么"（snapshot 那一行），所以它是第五种合法形状，不是裸布尔的另一种拼法。
    """
    return ValueError(
        f"action {i} 'screenshot' takes a CSS selector string, a bare snapshot row "
        'number, or an object with exactly one target: "selector" (CSS), "index" '
        '(from a snapshot), or "full_page": true (the whole page). Optional knobs: '
        'image_type, quality, decode_qr, save_to. A bare boolean is the shape of a '
        'different action: {"snapshot": true} lists the page\'s interactive elements '
        'and needs no target, while a capture always has to name something to look at.')


def _act_target(i: int, key: str, val) -> dict:
    """Normalize a click/hover/drag/fill/submit/read/screenshot target to exactly
    one of:

    ``{"selector": "css"}`` or ``{"index": N}``, plus the optional ``expect``
    name-check. Both spellings address the same element set — the CSS selector
    (the original path) and the snapshot index (MVP-2, "用户不用懂" via the
    outer agent) — and a dict carrying both is a typo, not a combination.
    A bare integer is the snapshot index written the way the snapshot showed it
    (刀9-6c · F8), so the number a caller reads off a row is the number it sends.

    `press` is not here: its bare value is already the KEY string ("Enter"), so a
    bare integer would read as a key name. Its target stays
    ``{"index": N, "key": "Enter"}``.
    """
    from dhole_mcp.security import validate_css_selector

    expect = ""
    if isinstance(val, str):
        if not val.strip():
            raise ValueError(f"action {i} {key!r} must be a non-empty CSS selector")
        validate_css_selector(val)
        return {"selector": val}
    if isinstance(val, int) and not isinstance(val, bool):
        # 刀9-6c（F8）：一个裸整数就是一个快照行号，`{"click": 3}` 与
        # `{"click": {"index": 3}}` 从此同义。这不是赏一个新写法，是收回一条会让人连犯
        # 四次的规则：`{"snapshot": true}` 交回来的那一列本身就写着 0/1/2/3，而抄那个数
        # 的人先写下的一定是裸数（2026-10-11 全真模拟 S10：hover 裸数✗、drag 落点✗、
        # screenshot 空 obj✗、confirm 位置✗ —— 同一类形状错连撞四次，第四次用户说
        # "又格式错了？"）。区间与布尔那两位仍由下面 dict 那一支裁决，这里不立第二个作者。
        val = {"index": val}
    if not isinstance(val, dict):
        raise ValueError(
            f"action {i} {key!r} must be a CSS selector string, "
            '{"selector": "css"} or {"index": N}')
    sel = val.get("selector")
    idx = val.get("index")
    if (sel is None) == (idx is None):
        raise ValueError(
            f"action {i} {key!r} needs exactly one of 'selector' (CSS) or "
            "'index' (from a {\"snapshot\": true} row)")
    raw_expect = val.get("expect")
    if raw_expect is not None:
        if not isinstance(raw_expect, str) or not raw_expect.strip() \
                or len(raw_expect) > 100:
            raise ValueError(
                f"action {i} {key!r}.expect must be a short string (max 100 "
                "chars) naming the element as the snapshot row showed it")
        expect = raw_expect.strip()
    if sel is not None:
        if not isinstance(sel, str) or not sel.strip():
            raise ValueError(
                f"action {i} {key!r}.selector must be a non-empty CSS selector")
        validate_css_selector(sel)
        target: dict = {"selector": sel}
    else:
        if not isinstance(idx, int) or isinstance(idx, bool) \
                or not (0 <= idx <= MAX_ELEMENT_INDEX):
            raise ValueError(
                f"action {i} {key!r}.index must be an int in 0..{MAX_ELEMENT_INDEX} "
                "(take it from a {\"snapshot\": true} row)")
        target = {"index": idx}
    if expect:
        target["expect"] = expect
    frame = val.get("frame") if isinstance(val, dict) else None
    if frame is not None:
        # iframe 内的元素。三种拼法在**解析时**（_target_locator）分派：frame 的
        # name 精确匹配、URL 前缀匹配、或 iframe 元素自己的 CSS。校验期只验形状
        # ——URL 里带 :// ，当 CSS 验会误杀合法拼法；CSS 那一支的真正校验挪到
        # 解析前一刻（resolve 里的 frame_locator 分支）。
        if not isinstance(frame, str) or not frame.strip() or len(frame) > 300:
            raise ValueError(
                f"action {i} {key!r}.frame must be a short string naming the "
                '<iframe>: its name, an absolute URL prefix (copy it from a '
                'snapshot frame row), or a CSS selector for the '
                '<iframe> element (e.g. {"selector": "#save", "frame": "#app"})')
        target["frame"] = frame.strip()
    return target


def _validate_actions(actions) -> list[dict]:
    """Validate + normalize the actions list. Raises ValueError on bad input."""
    from dhole_mcp.security import validate_css_selector

    if not isinstance(actions, list) or not actions:
        raise ValueError("actions must be a non-empty list of action dicts")
    if len(actions) > MAX_ACTIONS:
        raise ValueError(f"Too many actions ({len(actions)}). Maximum is {MAX_ACTIONS}.")
    out: list[dict] = []
    # `read` 登记过的名字（`as`），按链的顺序累积 —— `fill` 的 `{=名字}` 只能引用
    # **跑在它前面**的那一次读，否则那条引用指向的是还没发生的事。
    stored: set[str] = set()
    for i, a in enumerate(actions):
        if not isinstance(a, dict) or len(a) != 1:
            raise ValueError(
                f"action {i} must be a dict with exactly one key {sorted(_VALID_KEYS)}; "
                "the target rides inside that key's value: "
                '{"fill": {"index": 0, "text": "new value"}} or {"click": "#submit"}'
            )
        (key, val), = a.items()
        if key not in _VALID_KEYS:
            raise ValueError(f"action {i} has unknown key {key!r}; valid: {sorted(_VALID_KEYS)}")
        if key == "click":
            if isinstance(val, str):
                # The original spelling stays the original spelling: a string
                # passes through as a string, so nothing that reads normalized
                # actions sees a shape drift for the existing form.
                if not val.strip():
                    raise ValueError(f"action {i} {key!r} must be a non-empty CSS selector string")
                validate_css_selector(val)  # raises SecurityError on bad selector
                out.append({key: val})
            else:
                target = _act_target(i, key, val)
                # `confirm` 只长在 dict 那一支：裸行号（刀9-6c · F8）没有放它的格子，
                # 缺席即 False —— 与 `{"index": N}` 不写 confirm 时逐字同形。
                confirm = (val.get("confirm", False) if isinstance(val, dict)
                           else False)
                if not isinstance(confirm, bool):
                    raise ValueError(f"action {i} 'click.confirm' must be a boolean")
                # confirm 只在一个场景被读：目标解析成提交型控件时，它就是
                # submit 闸的钥匙（执行器里判定）。False 不落键——既有形状
                # 逐位不变，快照/回执/审计读到的归一化产物照旧。
                if confirm:
                    target = {**target, "confirm": True}
                out.append({key: target})
        elif key == "hover":
            # Same target grammar as click (string shorthand or the
            # {"selector"|"index", "expect"?} dict); no dict-only knobs exist.
            out.append({key: _act_target(i, key, val)})
        elif key == "drag":
            # 拖拽的语义是两个端点：被拖的元素和落点，所以字符串简写在这里
            # 不设——没有落点的 drag 不是没写完，是没意义（click 的简写是
            # 历史形状，drag 没有历史）。两端各自走同一套 _act_target 寻址，
            # 快照索引因此对被拖元素与落点都可用。
            def _drag_shape(why: str = "") -> ValueError:
                return ValueError(
                    f"action {i} 'drag' must be {{selector|index, to}} - the "
                    "element to drag plus where to drop it; 'to' takes the same "
                    "target shapes (a CSS selector string or "
                    '{"selector"|"index", "expect"?})' + why)
            if not isinstance(val, dict) or "to" not in val:
                raise _drag_shape()
            # 陌生键先于源端寻址裁决：{"from": …} 走 _act_target 只会报
            # "selector/index 二选一"那半句（R01-010 现场：第一拒不提 to，
            # 第二拒才见全形，三连拒才摸到正形）。第一拒就是全形。
            foreign = sorted(set(val) - {"selector", "index", "expect", "to"})
            if foreign:
                raise _drag_shape(f" - unexpected key(s): {foreign}")
            src = _act_target(i, key, {k: v for k, v in val.items() if k != "to"})
            to = _act_target(i, f"{key}.to", val["to"])
            out.append({key: {**src, "to": to}})
        elif key == "upload":
            # {"upload": {selector|index, files: [path, ...]}} — 把本机文件装进
            # <input type=file>。字符串简写在这里不设：files 没地方放。设置文件
            # 本身只是页面状态（fill 的同侪），真正的外发发生在表单提交那一刻
            # ——那一步照旧由 submit 闸把关，upload 不设第二道确认门。
            # 路径校验在**这里**（浏览器还没起来的时刻），与 save_to 同一条
            # 理由：拼错的路径值一次即时拒绝，不值一次 10 秒冷启动。
            if not isinstance(val, dict):
                raise ValueError(
                    f"action {i} 'upload' must be {{selector|index, files}} - "
                    'e.g. {"upload": {"selector": "input[type=file]", '
                    '"files": ["./report.pdf"]}}; files is required')
            target = _act_target(i, key, val)
            resolved = _resolve_upload_files(val.get("files"), f"action {i} 'upload'")
            out.append({key: {**target, "files": resolved}})
        elif key == "dialog":
            # {"dialog": "accept"|"dismiss"} 或 {"dialog": {"answer": ...,
            # "prompt_text": ...}} — 武装**下一个**弹出的对话框。缺省时页面弹的
            # 一切都按 Playwright 默认自动 dismiss（安全侧正确、但静默）；这一颗
            # 让 confirm/prompt 有人作答，弹没弹、谁答的、答了什么，链尾的账本行
            # 如实交回。没有"先弹后答"：Playwright 的对话框必须当场处置，
            # 武装必须在触发它的动作之前。
            if isinstance(val, str):
                answer, prompt_text = val, None
            elif isinstance(val, dict):
                answer = val.get("answer")
                prompt_text = val.get("prompt_text")
            else:
                answer, prompt_text = None, None
            if answer not in _DIALOG_ANSWERS:
                raise ValueError(
                    f"action {i} 'dialog' must be {{answer}} - a bare "
                    "\"accept\"/\"dismiss\", or {\"answer\": ..., "
                    '"prompt_text"?: ...} (the text that answers a prompt()) - '
                    "arm it BEFORE the action that pops the dialog")
            if prompt_text is not None and (
                    not isinstance(prompt_text, str) or len(prompt_text) > 200):
                raise ValueError(
                    f"action {i} 'dialog.prompt_text' must be a string of at "
                    "most 200 chars (it answers a prompt() dialog)")
            normalized: dict = {"answer": answer}
            if prompt_text is not None:
                normalized["prompt_text"] = prompt_text
            out.append({key: normalized})
        elif key == "tab":
            # {"tab": "new"|"close"|"list"|N} 或 {"tab": {"open": "https://..."}}。
            # 页签属于**这次调用的浏览器上下文**：new 开一枚（可带 open 的 URL）、
            # 整数切到第 N 枚（不导航）、close 关当前枚（关后回 0 号位；最后一枚
            # 拒关——链总要有一页来交答案）、list 交回清单。跨调用持久走 pin：
            # 钉住的调用把页签留进会话，下一次带 pin 的调用 {"tab": N} 直达。
            if isinstance(val, bool):
                raise ValueError(
                    f"action {i} 'tab' must be \"new\"|\"close\"|\"list\", a tab "
                    "number to switch to, or {{\"open\": \"https://...\"}} - a "
                    "bare boolean says nothing")
            if isinstance(val, str):
                if val not in _TAB_VERBS:
                    raise ValueError(
                        f"action {i} 'tab' must be \"new\"|\"close\"|\"list\", a "
                        "tab number, or {{\"open\": \"https://...\"}}; got "
                        f"{val!r}")
                out.append({key: val})
            elif isinstance(val, int):
                if not 0 <= val <= MAX_TABS:
                    raise ValueError(
                        f"action {i} 'tab' switch must be a tab number in "
                        f"0..{MAX_TABS} (take it from a tab list row)")
                out.append({key: val})
            elif isinstance(val, dict):
                if set(val) != {"open"}:
                    raise ValueError(
                        f"action {i} 'tab' object takes exactly one key: "
                        '{{"open": "https://..."}} - a new tab with that URL')
                target = val["open"]
                if not isinstance(target, str) or not target.strip():
                    raise ValueError(
                        f"action {i} 'tab.open' must be a non-empty URL")
                if not target.lower().startswith(("http://", "https://")):
                    raise ValueError(
                        f"action {i} 'tab.open' must be an http(s) URL, got "
                        f"{target!r}")
                out.append({key: {"open": target.strip()}})
            else:
                raise ValueError(
                    f"action {i} 'tab' must be \"new\"|\"close\"|\"list\", a tab "
                    "number, or {{\"open\": \"https://...\"}}")
        elif key == "navigate":
            # {"navigate": "back"|"forward"} — 历史导航。没有别的旋钮：等多少、
            # 等到什么程度，链的收尾纪律（DOM 安静）已经有自己的答案。
            if val not in _NAVIGATE_VERBS:
                raise ValueError(
                    f"action {i} 'navigate' must be \"back\" or \"forward\"; got "
                    f"{val!r} - a re-goto of the URL is what web_act always does "
                    "at chain start, and it is a FRESH page (form wiped); history "
                    "is the way back to the one the browser already holds")
            out.append({key: val})
        elif key == "snapshot":
            # {"snapshot": true} — 编号交互元素列表。无参数形态是刻意的：快照
            # 没有可调的东西（上限是保护，不是旋钮），多一个键就多一种拼错法。
            if val is not True:
                raise ValueError(
                    f"action {i} 'snapshot' must be true - usage: "
                    '{"snapshot": true}. The element list arrives in '
                    "metadata.actions as a receipt row; act on it with "
                    '{"click": {"index": N}}')
            out.append({key: True})
        elif key == "submit":
            # {"submit": {"selector"|"index", "confirm"?: true}} — 显式提交动作。
            # confirm 缺省 = 执行时暂停并返回 pending_approval（破坏性操作必须
            # 经用户确认，安全红线 3）；confirm=true = agent 已向用户转达并拿到
            # 确认后才该传的值。
            if isinstance(val, int) and not isinstance(val, bool):
                # 裸行号（刀9-6c · F8）= 提交第 N 行那个表单。简写只替掉寻址那一层，
                # confirm 缺席仍然是缺席 —— 破坏性操作的暂停门不因少写一层花括号而松动。
                val = {"index": val}
            if not isinstance(val, dict):
                raise ValueError(
                    f"action {i} 'submit' must be {{selector, confirm?}} or "
                    "{index, confirm?} - a submit is an irreversible step and "
                    "is never implicit in a click")
            target = _act_target(i, key, {k: v for k, v in val.items()
                                          if k != "confirm"})
            confirm = val.get("confirm", False)
            if not isinstance(confirm, bool):
                raise ValueError(f"action {i} 'submit.confirm' must be a boolean")
            out.append({key: {**target, "confirm": confirm}})
        elif key == "wait_selector":
            # A bare selector waits for the FIRST match to exist, which is the
            # shape this action has always had. The dict adds the two things a
            # lazy-loaded list actually needs: how many items, and whether they
            # must be painted (an appended-but-hidden node is not content yet).
            if isinstance(val, str):
                sel, count, state, tmo = val, 1, "attached", 10_000
            elif isinstance(val, dict):
                sel = val.get("selector")
                count = val.get("count", 1)
                state = val.get("state", "attached")
                tmo = val.get("timeout_ms", 10_000)
            else:
                raise ValueError(
                    f"action {i} 'wait_selector' must be a CSS selector string or "
                    f"{{selector, count, state, timeout_ms}}")
            if not isinstance(sel, str) or not sel.strip():
                raise ValueError(f"action {i} 'wait_selector.selector' must be a non-empty CSS selector")
            validate_css_selector(sel)
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ValueError(f"action {i} 'wait_selector.count' must be an int >= 1")
            if state not in _WAIT_STATES:
                raise ValueError(
                    f"action {i} 'wait_selector.state' must be one of {sorted(_WAIT_STATES)}")
            if not isinstance(tmo, (int, float)) or isinstance(tmo, bool):
                raise ValueError(f"action {i} 'wait_selector.timeout_ms' must be a number")
            out.append({key: {"selector": sel, "count": min(count, 1000),
                              "state": state, "timeout": max(500, min(int(tmo), MAX_WAIT_MS))}})
        elif key == "screenshot":
            # String shorthand = selector only; the dict adds the capture knobs.
            # The gate is checked HERE (validation time), so a refused capture
            # costs an instant error instead of a browser cold start.
            if isinstance(val, str):
                spec: dict = {"selector": val}
            elif isinstance(val, int) and not isinstance(val, bool):
                # 裸行号（刀9-6c · F8）：拍 snapshot 那一行，与 {"index": N} 同义。
                spec = {"index": val}
            elif isinstance(val, dict):
                spec = dict(val)
            else:
                raise _screenshot_shape_error(i)
            want_qr = spec.pop("decode_qr", False)
            if not isinstance(want_qr, bool):
                raise ValueError(f"action {i} 'screenshot.decode_qr' must be a boolean")
            full_page = spec.pop("full_page", False)
            if not isinstance(full_page, bool):
                raise ValueError(f"action {i} 'screenshot.full_page' must be a boolean")
            image_type = spec.pop("image_type", "png")
            if image_type not in ("png", "jpeg"):
                raise ValueError(
                    f"action {i} 'screenshot.image_type' must be 'png' or 'jpeg'")
            quality = spec.pop("quality", None)
            if quality is not None and (
                    not isinstance(quality, int) or isinstance(quality, bool)
                    or not 0 <= quality <= 100):
                raise ValueError(
                    f"action {i} 'screenshot.quality' must be an integer 0-100")
            if quality is not None and image_type != "jpeg":
                raise ValueError(
                    f"action {i} 'screenshot.quality' is only valid when "
                    "'screenshot.image_type' is 'jpeg'")
            save_to = spec.pop("save_to", None)
            if save_to is not None and (not isinstance(save_to, str)
                                        or not save_to.strip()):
                raise ValueError(
                    f"action {i} 'screenshot.save_to' must be a non-empty path "
                    "when present")
            if save_to is not None:
                # 与 web_screenshot 时代同一裁决：坏路径要一次立刻拒绝，而不是
                # 付完浏览器冷启动才在写盘处炸。执行器落盘前会再走一遍同一条
                # 校验（同一函数，同一裁决），这里提前的是"及时死"。
                _resolve_save_to(save_to, image_type)
            # full_page 与元素裁剪互斥（同一裁决自 web_screenshot 原样随迁）：
            # 元素裁剪本就被元素自己的盒子框住，没有"整页的元素"可言。
            if full_page:
                if spec.get("selector") or spec.get("index"):
                    raise ValueError(
                        f"action {i} 'screenshot.full_page' cannot combine with a "
                        "selector/index target - an element crop is already "
                        "bounded by the element's own box")
                out.append({"screenshot": {"full_page": True,
                                           "image_type": image_type,
                                           **({"quality": quality} if quality is not None else {}),
                                           "decode_qr": want_qr,
                                           **({"save_to": save_to} if save_to else {})}})
            else:
                if "selector" not in spec and "index" not in spec:
                    # 没有目标的 dict 与 bare true 是同一类形状错误：同一句报错，
                    # 不去借 click/fill 那句「selector 或 index」（那句不知道
                    # full_page 也是一种合法形状）。
                    raise _screenshot_shape_error(i)
                out.append({"screenshot": {**_act_target(i, key, spec),
                                           "image_type": image_type,
                                           **({"quality": quality} if quality is not None else {}),
                                           "decode_qr": want_qr,
                                           **({"save_to": save_to} if save_to else {})}})
        elif key == "pdf":
            # {"pdf": {"save_to": "..."}} — 把**当前渲染页**打印成 PDF 落盘。截图
            # 交像素、它交矢量（文字可选中，doc_parse 能读回来）；字节走盘不走
            # transcript（与 screenshot.save_to 同一条理由）。路径在这里验
            # （浏览器起来之前），理由与 screenshot 那句相同：拼错的路径值一次
            # 即时拒绝，不值一次 10 秒冷启动。
            if not isinstance(val, dict) or set(val) != {"save_to"}:
                raise ValueError(
                    f"action {i} 'pdf' must be {{save_to}} - the rendered page "
                    "printed to a PDF file on disk; bytes never ride the "
                    "transcript")
            resolved = _resolve_pdf_save_to(val["save_to"])
            out.append({key: {"save_to": val["save_to"].strip(),
                              "resolved": str(resolved)}})
        elif key == "fill":
            if not isinstance(val, dict) or "text" not in val:
                raise ValueError(f"action {i} 'fill' must be {{selector, text}} "
                                 "or {index, text}")
            text = val["text"]
            if not isinstance(text, str):
                raise ValueError(f"action {i} 'fill.text' must be a string")
            if len(text) > MAX_FILL_TEXT_CHARS:
                raise ValueError(
                    f"action {i} 'fill.text' too long "
                    f"(max {MAX_FILL_TEXT_CHARS} chars)")
            # `{=名字}` 引用的是**同一条链里更早那次 read** 读回来的文本。名字不在
            # 已登记的那一里就当场具名拒绝：把 `{=cod}` 原样填进表单，交给网站的就是
            # 那六个字符，而那一发看起来是一次成功的 fill。
            for name in _REF_RE.findall(text):
                if name not in stored:
                    _where = (f" (available: {sorted(stored)})" if stored else
                              " (no {\"read\": ...} action runs before this one)")
                    _pin_way = ("" if stored else
                                f" - registers live for this one call: mint the name in "
                                f"THIS chain with an earlier read step's as={name} "
                                f"before the fill; a pinned page "
                                f"(pin=true) holds the page open, so a re-read in the next "
                                f"call's chain returns the same token")
                    raise ValueError(
                        f"action {i} 'fill.text' references {{={name}}} but no "
                        f"earlier action stored that name{_where}{_pin_way}")
            target = _act_target(
                i, key, {k: v for k, v in val.items() if k != "text"})
            out.append({key: {**target, "text": text}})
        elif key == "select":
            # {"select": {selector|index, option}} — <select> 下拉框。fill 在这里
            # 结构性够不到（Playwright 的 fill 只认 input/textarea/contenteditable，
            # 对 <select> 直接抛错），click 打开的是操作系统的原生下拉、没有可点的
            # DOM。option 先按 value 匹配、再按可见标签（执行器里的 _SELECT_RESOLVE_JS），
            # 一颗字符串选一枚；列表只在 multiple 的 <select> 上合法（校验期不知道
            # 页面的形状，这一裁决在执行器里、拿着页面真身做）。
            if not isinstance(val, dict) or "option" not in val:
                raise ValueError(f"action {i} 'select' must be {{selector|index, option}} "
                                 "- option is the option's value or its visible "
                                 "label; a list chooses several (multi-select only)")
            option = val["option"]
            opts = [option] if isinstance(option, str) else option
            if (not isinstance(opts, list) or not opts
                    or not all(isinstance(o, str) and o.strip() for o in opts)):
                raise ValueError(
                    f"action {i} 'select.option' must be a non-empty option string "
                    "or a non-empty list of them")
            target = _act_target(
                i, key, {k: v for k, v in val.items() if k != "option"})
            out.append({key: {**target, "option": opts}})
        elif key == "read":
            # 链内读（A3-b）：取回**当前这个页面实例**上某一处的文本，进回执，
            # 登记成名字后供后面的 fill 用 `{=名字}` 取用。
            #
            # 它解决的是那一种挑战：答案就写在 DOM 里（"请抄写下方的字符"），而调用方
            # 读得到却够不着 —— 跨调用重跑会重新导航，挑战换一枚，第二次填进去的是上一
            # 枚实例的答案（实测靶站 `/captcha` 每次 GET 出新 token）。同一条链里
            # read → fill → submit 是唯一"看到的就是答的那一个页面"的形状；
            # `screenshot` 顶替不了它，因为那一张图要到整条链跑完才交得到调用方眼前。
            target = _act_target(i, key, val)
            if isinstance(val, dict):
                attr = val.get("attribute")
                prop = val.get("property")
                if attr is not None and prop is not None:
                    # 同 _act_target 的规矩：一个 dict 里带两个来源是笔误，不是组合。
                    # 读法只有"标签上那一层"和"页面上现在这一层"两种，同时要就无从解释。
                    raise ValueError(
                        f"action {i} 'read' takes either 'attribute' (the markup's own "
                        f"value) or 'property' (what the page holds now), not both "
                        f"(got attribute={attr!r} property={prop!r})")
                if attr is not None:
                    if not isinstance(attr, str) or not _ATTR_RE.fullmatch(attr):
                        raise ValueError(
                            f"action {i} 'read.attribute' must be an attribute name "
                            f"like \"value\" or \"href\" (got {attr!r})")
                    target["attribute"] = attr
                if prop is not None:
                    if not isinstance(prop, str) or not _READ_PROPERTY_RE.fullmatch(prop):
                        raise ValueError(
                            f"action {i} 'read.property' must be one of "
                            "\"value\", \"checked\", \"selected\" — the fields a fill "
                            f"actually changes (got {prop!r})")
                    target["property"] = prop
                as_name = val.get("as")
                if as_name is not None:
                    if not isinstance(as_name, str) or not _AS_RE.fullmatch(as_name):
                        raise ValueError(
                            f"action {i} 'read.as' must be a short name "
                            "(a-z, 0-9, underscore, max 31) that a later "
                            f"fill can reference as {{=name}} (got {as_name!r})")
                    stored.add(as_name)
                    target["as"] = as_name
            out.append({key: target})
        elif key == "press":
            if isinstance(val, str):
                k = val.strip()
                if not k:
                    raise ValueError(f"action {i} 'press' key is empty")
                out.append({key: {"selector": None, "key": k[:50]}})
            elif isinstance(val, dict) and "key" in val:
                sel = val.get("selector")
                idx = val.get("index")
                k = str(val.get("key", "")).strip()
                if not k:
                    raise ValueError(f"action {i} 'press.key' is empty")
                if sel is not None and idx is not None:
                    raise ValueError(
                        f"action {i} 'press' takes 'selector' or 'index', not both")
                if sel is not None:
                    if not isinstance(sel, str) or not sel.strip():
                        raise ValueError(f"action {i} 'press.selector' must be a non-empty CSS selector")
                    validate_css_selector(sel)
                    out.append({key: {"selector": sel, "key": k[:50]}})
                elif idx is not None:
                    if not isinstance(idx, int) or isinstance(idx, bool) \
                            or not (0 <= idx <= MAX_ELEMENT_INDEX):
                        raise ValueError(
                            f"action {i} 'press.index' must be an int in "
                            f"0..{MAX_ELEMENT_INDEX}")
                    out.append({key: {"index": idx, "key": k[:50]}})
                else:
                    out.append({key: {"selector": None, "key": k[:50]}})
            else:
                raise ValueError(f"action {i} 'press' must be a key string or {{selector, key}}")
        elif key == "wait":
            # A digit STRING is accepted because that is how a client that
            # serializes numbers spells {"wait": 1000} — as {"wait": "1000"} —
            # and rejecting it failed the whole fetch over a formatting detail.
            out.append({key: _int_arg(val, f"action {i} 'wait'", "ms", MAX_WAIT_MS)})
        elif key == "scroll":
            # One normalized shape, so the executor has a single path.
            spec = val if isinstance(val, dict) else {}
            steps = spec.get("steps", 3) if isinstance(val, dict) else val
            sel = spec.get("selector")
            ms = spec.get("ms_per_step", SCROLL_STEP_MS_DEFAULT)
            if sel is not None and (not isinstance(sel, str) or not sel.strip()):
                raise ValueError(
                    f"action {i} 'scroll.selector' must be a non-empty CSS selector")
            if sel:
                validate_css_selector(sel)
            if not isinstance(ms, (int, float)) or isinstance(ms, bool):
                raise ValueError(f"action {i} 'scroll.ms_per_step' must be a number")
            ms_applied = max(250, min(int(ms), SCROLL_STEP_MS_MAX))
            if ms_applied != int(ms):
                _note_text(_clamp_note(f"action {i} scroll.ms_per_step", int(ms),
                                       ms_applied, lo=250, hi=SCROLL_STEP_MS_MAX))
            out.append({"scroll": {
                "steps": _int_arg(steps, f"action {i} 'scroll'", "steps", MAX_SCROLL),
                "selector": sel or None,
                "ms_per_step": ms_applied,
            }})
    return out


_WAIT_STATES = frozenset({"attached", "visible", "hidden", "detached"})




def _int_arg(val, where: str, unit: str, cap: int) -> int:
    """Coerce an int-or-digit-string action argument into a clamped int.

    The cap is announced (`{"wait": 120000}` became 30,000 ms and the action list
    came back looking exactly like what was sent), because the caller is the one
    budgeting wall-clock time for a page: a wait that is shorter than asked is a
    fact about the result, not a detail of the implementation.
    """
    n: int | None = None
    if isinstance(val, int) and not isinstance(val, bool):
        n = val
    elif isinstance(val, str):
        try:
            n = int(val.strip())
        except ValueError:
            n = None
    if n is None:
        raise ValueError(f"{where} must be an int ({unit})")
    applied = max(0, min(n, cap))
    if applied != n:
        _note_text(f"{where} clamped {n}->{applied} {unit} "
                   f"(supported range 0-{cap})")
    return applied


_MEASURE_JS = """
(sel) => {
  const el = sel ? document.querySelector(sel) : null;
  const root = el || document.scrollingElement || document.documentElement;
  return [Math.round(root.scrollTop || 0), Math.round(root.scrollHeight || 0),
          (el || document).getElementsByTagName('*').length];
}
"""

_SCROLL_JS = """
(sel) => {
  const el = sel ? document.querySelector(sel) : null;
  if (el) {
    el.scrollTop = el.scrollHeight;
    el.dispatchEvent(new Event('scroll', {bubbles: true}));
    return true;
  }
  const root = document.scrollingElement || document.documentElement;
  window.scrollTo(0, (root.scrollHeight || 0) + window.innerHeight);
  // A lazy loader runs on the scroll EVENT, and if the document is not
  // scrollable — a tall viewport on short, stylesheet-less content — scrollTo
  // changes nothing and NO event fires, so the loader never wakes and the page
  // sits at its first chunk forever. Measured on quotes.toscrape.com/scroll:
  // scrollY stayed 0 through 6 steps. Dispatching the event is what the scroll
  // was supposed to cause; `resize` covers the loaders that recompute on layout.
  window.dispatchEvent(new Event('scroll'));
  document.dispatchEvent(new Event('scroll'));
  window.dispatchEvent(new Event('resize'));
  return true;
}
"""

_COUNT_JS = "(p) => document.querySelectorAll(p[0]).length >= p[1]"

# (where we are, how much page there is). The three numbers answer the only
# question a click leaves open: did something happen, and is it still happening.
_DOM_MARK_JS = """() => [location.href,
                        document.querySelectorAll('*').length,
                        document.body ? document.body.innerText.length : 0]"""


async def _dom_snapshot(page):
    """``_DOM_MARK_JS``'s three numbers, or None when there is no page to ask.

    A document being replaced mid-navigation has no execution context to
    evaluate against, and that error is itself the answer — the caller treats
    None as "a navigation is in flight", which is the case worth waiting for.
    """
    try:
        got = await page.evaluate(_DOM_MARK_JS)
    except Exception:
        return None
    return tuple(got) if isinstance(got, list) else None


async def _frame_locator(page, frame: str, sel: str):
    """frame 寻址的三种拼法 → locator：name 精确匹配、URL 前缀、iframe 的 CSS。

    顺序即优先级：名字最具体，URL 前缀次之（快照的 frame 行交回的就是 URL，
    抄那一行的前缀是最省事的拼法——**含 scheme 的绝对前缀**，相对路径进不了
    startswith），CSS 最后——只有它走 `validate_css_selector`
    的真校验（URL 里带 ://，当 CSS 验会误杀）。跨域 iframe 照样可达：frame_locator
    / Frame.locator 都过 CDP，不受同源策略约束。

    CSS 支落地前先数一遍顶层文档：0 个 <iframe> 匹配（或这串根本不是合法 CSS）
    就地具名拒绝——R08-001/R08-005 的现场：相对前缀炸成 Playwright 的 CSS 解析
    错、错名等到 10s 超时后被 selector-miss 句顶替，两处的矛头都指错了对象。
    """
    try:
        frames = [f for f in page.frames if f is not page.main_frame]
    except Exception:
        frames = []
    for f in frames:
        if f.name == frame:
            return f.locator(sel).first
    for f in frames:
        if (f.url or "").startswith(frame):
            return f.locator(sel).first
    from dhole_mcp.security import validate_css_selector
    validate_css_selector(frame)
    try:
        hits = await page.locator(frame).count()
    except Exception as exc:
        first = (str(exc).strip().splitlines() or [""])[0][:160]
        raise ValueError(
            f"frame {frame!r} is not a usable address - it is neither an iframe "
            "name, an absolute URL prefix (copy it from a snapshot frame row), "
            f"nor a CSS selector for the <iframe>: the CSS parse failed ({first})") from exc
    if not hits:
        present = [f"{f.name or '?'} -> {f.url}" for f in frames]
        raise ValueError(
            f"frame {frame!r} matched 0 <iframe> element(s) in the top document - "
            f"the iframes present: {present}. frame= takes the iframe's name, an "
            "absolute URL prefix (copy it from a snapshot frame row), or a CSS "
            "selector for the <iframe>")
    return page.frame_locator(frame).locator(sel).first


async def _target_locator(page, target):
    """Resolve a validated action target (a bare selector string, {"selector"}
    or {"index", "expect"?}) to a Playwright locator, running the `expect`
    name-check first.

    The check is what makes index addressing safe across two page loads: a
    stale index must fail LOUDLY (naming what the element reads now) instead of
    quietly clicking something else. It costs one extra roundtrip only when
    `expect` was passed.

    The bare-string form is click's original shape (`{"click": "a.more-link"}`);
    validation passes it through unchanged, so resolution has to accept it.
    """
    if isinstance(target, str):
        return page.locator(target).first
    frame = target.get("frame")
    if "index" in target and target.get("index") is not None:
        if target.get("expect"):
            await _check_expect(page, target["index"], target["expect"])
        loc = await resolve_index(page, target["index"])
        return loc
    sel = target.get("selector")
    if not sel:
        raise ValueError(f"action target has neither selector nor index: {target!r}")
    if frame:
        loc = await _frame_locator(page, frame, sel)
    else:
        loc = page.locator(sel).first
    if target.get("expect"):
        try:
            name = await loc.evaluate(_NAME_JS)
        except Exception:
            name = ""
        if target["expect"].strip().lower() not in str(name).strip().lower():
            raise ValueError(
                f"element {sel!r} reads {str(name)[:80]!r}, expected "
                f"~{target['expect']!r} - check the selector against a fresh "
                "copy of the page")
    return loc


async def _click_and_settle(page, selector: str) -> None:
    """Click, then give whatever the click caused time to finish."""
    await _settle_after_click(page, page.locator(selector).first)


async def _settle_after_click(page, click_locator) -> None:
    """``locator().click()`` returns once the event is dispatched, not once its
    consequence is done, so every click that navigated raced the extraction
    (measured on HN's ``a.more-link``: the old page came back). Two shapes to
    wait out, and they are different questions:

    * the destination changed — a navigation, in flight or already swapped the
      document out (``_dom_snapshot`` going None). Wait for the new document.
    * the same page grew — an expander or an in-page load. Wait until it stops
      changing.

    A click nothing answers gets out at ``CLICK_SETTLE_MIN_MS``.
    """
    before = await _dom_snapshot(page)
    await click_locator.click(timeout=_step_deadline_ms())
    started = time.monotonic()
    # 这一颗重新读一次余额：上面那发 click 已经花掉了一截，settle 拿的是**剩下的**，
    # 不是同一个 4 秒的再一遍 —— 两步各自合规、加起来越过调用方点名的预算，正是场景 10
    # 那句"你要 60000，我给你 61529 加 0 字节"的形状。
    deadline = started + _step_deadline_ms(CLICK_SETTLE_MS) / 1000.0
    quiet_polls = 0
    navigated = False
    while time.monotonic() < deadline:
        await page.wait_for_timeout(200)
        now = await _dom_snapshot(page)
        if now is None:
            navigated = True
            break
        if before is not None and now[0] != before[0]:
            navigated = True
            break
        if now == before:
            quiet_polls += 1
            if (quiet_polls >= 2
                    and (time.monotonic() - started) * 1000 >= CLICK_SETTLE_MIN_MS):
                break
        else:
            before, quiet_polls = now, 0
    if navigated:
        left_ms = int(max(250.0, (deadline - time.monotonic()) * 1000))
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=left_ms)


# hover / drag 的后果是页内变化（悬停菜单展开、可排序列表重排），不是导航。等法
# 与 click 收尾的"安静轮询"同一问——DOM 什么时候停止变化——但没有"换页了吗"
# 那一问，上限也更短：菜单是同步渲染的本地变换，没有一个网络往返可等。
QUIET_SETTLE_MS = 2_000
QUIET_SETTLE_MIN_MS = 300


async def _settle_quiet(page) -> None:
    """hover / drag 的收尾：等页内后果落定（DOM 安静两拍或到预算）。"""
    before = await _dom_snapshot(page)
    started = time.monotonic()
    deadline = started + QUIET_SETTLE_MS / 1000.0
    quiet = 0
    while time.monotonic() < deadline:
        await page.wait_for_timeout(200)
        now = await _dom_snapshot(page)
        if now is not None and now == before:
            quiet += 1
            if (quiet >= 2
                    and (time.monotonic() - started) * 1000 >= QUIET_SETTLE_MIN_MS):
                return
        else:
            before, quiet = now, 0


async def _scroll(page, steps: int, selector, ms_per_step: int) -> None:
    """Reach the bottom, wait for the page to answer, then go again.

    The old form was ``scrollBy(0, innerHeight)`` plus a fixed 700 ms nap, and on
    a real infinite-scroll page it produced nothing at all — measured on
    quotes.toscrape.com/scroll: 10 of those steps left the page at its first 10
    items with "Loading..." still up. Three reasons, all fixed here:

    * a lazy loader keys off the distance to the bottom, and every chunk it
      appends moves that bottom away again — so one scroll per step stops at the
      OLD bottom and never asks for more. Each step now re-scrolls whenever the
      DOM grows.
    * the loader runs on the scroll EVENT. When the document is not scrollable
      (a tall viewport over short, stylesheet-blocked content — scrollY measured
      0 through every step) a scrollTo changes nothing and fires nothing, so the
      loader never wakes. The scroll now dispatches the event it was meant to
      cause.
    * 700 ms is a guess about a network round trip. Waiting for the DOM to stop
      changing answers the question that actually matters, and gives up as soon
      as the page has gone quiet.

    The whole action spends a bounded slice of the call budget
    (``SCROLL_TOTAL_MS``): a feed that never stops growing must not become a
    fetch that never returns. Measured after all three: 10 items -> 100 (the
    whole feed) on the page that used to return 10.
    """
    deadline = time.monotonic() + _step_deadline_ms(SCROLL_TOTAL_MS) / 1000.0
    arg = selector or ""
    for _ in range(max(0, int(steps))):
        if time.monotonic() >= deadline:
            logger.info("scroll hit its time budget (%d steps asked)", steps)
            return
        before = await page.evaluate(_MEASURE_JS, arg)
        await page.evaluate(_SCROLL_JS, arg)
        step_deadline = min(deadline, time.monotonic() + ms_per_step / 1000.0)
        stable = 0
        while time.monotonic() < step_deadline:
            await page.wait_for_timeout(250)
            now = await page.evaluate(_MEASURE_JS, arg)
            if now != before:
                before = now
                stable = 0
                await page.evaluate(_SCROLL_JS, arg)  # chase the bottom it just moved
            else:
                stable += 1
                if stable >= 2:
                    break


async def _is_submit_control(loc) -> bool:
    """True when the click target would submit a form (评审R1刀1).

    闸认动作效果，不认动作名：input[type=submit|image] 与 form 关联的
    submit 型 button，点击的后果与 ``{"submit": ...}`` 完全相同，就该过
    同一道门。探测失败不发明闸——元素等不到（TimeoutError 原样传播：click
    本来也会在这里超时，``_action_failure`` 给同一句友好文案，不双倍等）
    或页面读不出类型（返回 False，click 走既有路径），都不虚构一道拦不住
    任何东西的关卡。
    """
    try:
        await loc.wait_for(state="attached", timeout=_step_deadline_ms())
        kind = await loc.evaluate(_SUBMIT_CONTROL_JS)
    except Exception as exc:
        # 按类名判而非 isinstance：playwright 的 TimeoutError 不继承内建
        # TimeoutError，但类名就是它。
        if type(exc).__name__ == "TimeoutError":
            raise
        return False
    return kind == "submit"


#: 提交闸那一步**唯一可抄**的形状，两处出路都引用这串字（评审 R02-028）。从前一处写在
#: f-string 里（花括号转义生效），另一处写在普通字符串拼接里（转义没人处理，双花括号原样
#: 上线）—— 同一封响应给出两种抄法，抄哪一种都可能撞一次非法 JSON。
#: 2026-10-25 R01-006 补测：裸的 `{"confirm": true}` 不说层次，连读着源码的审查员都先
#: 把它当顶层参数（顶层 confirm=true 不动闸）；正形长在**动作里**。提示句必须把层说出去。
_CONFIRM_SNIPPET = ('confirm: true on the click/submit action itself '
                    '({"click": {"selector": "...", "confirm": true}}), '
                    'not at the top level')


async def _submit_gate_row(label: str, confirm: bool, rerun: str,
                           pinned: bool = False) -> Optional[dict]:
    """提交审批闸（安全红线 3），``{"submit": ...}`` 与提交型控件上的 click
    共用（评审R1刀1）。

    返回要追加的 pending 行（调用方 append 后 continue），None = 放行。
    两个分支与批次8 刀1 的语义逐位一致：声明了 elicitation 的连接上人按键
    授权（out-of-band ask），decline / 问不到人一律 fail-closed——confirm
    参数救不回来；能力为假 / 无会话 → 参数 confirm 是唯一的钥匙。rerun 是
    pending 行里教的重跑写法（submit 与 click 的 confirm 落点不同）。

    `pinned`（刀9-7 · F9）只往那句话上**追加**一条事实，默认串的每一个字不动：
    站在驻留页上时，"把同一串动作再跑一遍"是假话 —— 前面那些 fill 已经跑过、
    表单还填着、页也可能已经不是刚进来的时候那一页。所以这里给它第二条出路
    （只重跑被拦下的这一步 / 去掉 pin 从新页开始），而不是让人去猜哪一种是对的。
    """
    human = await out_of_band_confirm(
        f"Approve ONE form submission on this page (target: {label})? "
        "The submission is irreversible once sent. Nothing is submitted "
        "without your approval.")
    if human is False:
        return {
            "action": label, "ok": False, "pending_approval": True,
            "error": ("pending_approval: the approval ask was declined or "
                      "could not reach a human - nothing was submitted. dhole "
                      "asks again on any re-run of this chain; there is no "
                      "parameter that skips the ask."),
        }
    if human is None and not confirm:
        return {
            "action": label, "ok": False, "pending_approval": True,
            "error": (f"pending_approval: submitting is an irreversible step "
                      f"- ask the user to confirm this submission, then "
                      f're-run the same actions with {_CONFIRM_SNIPPET} '
                      f"{rerun}. Nothing was submitted."
                      + ("" if not pinned else
                         " · THIS PAGE IS PINNED (pin=true), so that sentence is "
                         f"only half right: the steps before this one already ran "
                         f"on the page you are standing on (a form still holds what "
                         f"they filled). Two honest ways: re-run just this submit step "
                         f"with {_CONFIRM_SNIPPET} on the same pinned session, or "
                         f"drop pin=true to start from a fresh navigation (a fresh "
                         f"challenge, a blank form)"))
        }
    return None


def _target_label(t) -> str:
    """One readable token for an action target: selector, or [index] (+expect)."""
    if isinstance(t, str):
        return repr(t)
    if (t or {}).get("index") is not None:
        base = f"[{t['index']}]"
        return base + (f" ~{t['expect']}" if t.get("expect") else "")
    return repr((t or {}).get("selector"))


# `action_label` 在**执行之前**渲染，所以它能把"没有 confirm 旋钮"写成"(unconfirmed)"
# —— pending 行带着这句话是对的，那一步确实没发出去。发出去的那一行不能再带着它。
_UNCONFIRMED = " (unconfirmed)"

#: 一次"交表"在网络上长什么样：载入一份新文档（任何方法，含 method=get 的表单），
#: 或脚本自己发的写请求。`beacon` / 图片 / 埋点那一类不算证据 —— 它们恰好在点击时也会发。
_EVIDENCE_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_EVIDENCE_WRITE_TYPES = ("xhr", "fetch")


class _SubmissionWitness:
    """这一页真的往外发了东西吗 —— `submitted` 这个词唯一的依据。

    实测（2026-10-08 真机，本地靶站）：`{"submit": {"selector": "form"}}` 点的是一棵
    `<FORM>`，它不会被"点击"提交，页面一步没动，而这一支在 `_settle_after_click` 返回后
    无条件写 `submitted: true`。后果不止于读者被骗 —— 这一行正是 DHOLE_USAGE_LOG
    `browser_submit` 通道读的载荷，于是写审计里多了一笔根本没发生的写操作；同一发里换成
    按钮选择器就真收到了 POST，两相对照才看得见那一格一直在猜。

    只认**请求**，不认地址栏：`history.pushState` 让 URL 变而一个字节都不发，而那正是
    这一格从前会误报的形状。代价是 `<form target=_blank>` 那一类（新页的请求挂在另一个
    Page 上）会被说成没出去 —— 少报一笔写得回（换个选择器就看见），多报一笔写进审计就
    拿不回来了。
    """

    def __init__(self, page: Any) -> None:
        self.page = page
        self.evidence: list[str] = []
        self._listening = False

    def _add(self, token: str) -> None:
        if token not in self.evidence:
            self.evidence.append(token)

    def _record(self, request) -> None:
        try:
            method = (request.method or "GET").upper()
            rtype = getattr(request, "resource_type", "") or ""
        except Exception:
            return
        if rtype == "document":
            self._add(method)
        elif method in _EVIDENCE_WRITE_METHODS and rtype in _EVIDENCE_WRITE_TYPES:
            self._add(method)

    def start(self) -> "_SubmissionWitness":
        on = getattr(self.page, "on", None)
        if callable(on):
            try:
                on("request", self._record)
                self._listening = True
            except Exception:
                self._listening = False
        return self

    def stop(self) -> list[str]:
        off = getattr(self.page, "remove_listener", None)
        if self._listening and callable(off):
            with contextlib.suppress(Exception):
                off("request", self._record)
        return self.evidence


def _no_submission_note(tag: str) -> str:
    """那一发没出去时怎么说：点名它落在什么元素上，并给两条真的能交表的路。"""
    landed = (f" - it landed on a <{tag}>, which no click submits" if tag else "")
    return (f"no_submission: nothing left this page{landed}. Name the control that "
            'submits instead (a button or an input[type=submit]), or press Enter in '
            'a field: {"press": {"selector": "<the field>", "key": "Enter"}}. The '
            "page did not move, so re-run the whole chain once it does.")


async def _no_submission_row(label: str, loc) -> dict:
    """一行"这一发没出去"。`submitted` 因此**不设** —— 写审计（quality 的
    browser_submit 通道）读的就是那个键，把没发生的写操作记进去比少记一行更糟。
    """
    tag = ""
    with contextlib.suppress(Exception):
        tag = str(await loc.evaluate(_TAG_JS) or "")
    return {"action": label, "ok": False, "not_submitted": True,
            "error": _no_submission_note(tag.lower())}


async def _settle_after_submit(page, loc) -> list[str]:
    """点下去、等它落定，并把"这一发真的出去了"的证据带回来。

    Witness 挂在点击**之前**：request 事件在 settle 轮询观察到导航之前就走完了，事后
    补听只会听到下一发。
    """
    witness = _SubmissionWitness(page).start()
    await _settle_after_click(page, loc)
    return witness.stop()


def _permitted(label: str) -> str:
    """闸放行之后，"(unconfirmed)" 就不再多说了 —— 它说的是"这一步还没被允许"。

    `action_label` 在**执行之前**渲染，那时人按键 / `confirm` 都还不在场；放行之后这一
    步的每一种结局（真发出去了、点了却没出去、点击本身失败）都已经是"被允许过的一步"，
    再带着那半句就成了假话 —— 而这一行正是 DHOLE_USAGE_LOG 写审计读的载荷，审计存在的
    意义就是回答"这一步是谁允许的"。被闸拦下的那一行不走这里：后缀归它留着。
    """
    return label[:-len(_UNCONFIRMED)] if label.endswith(_UNCONFIRMED) else label


def action_label(a: dict) -> str:
    """One short line naming an action, for the outcome receipt in the response."""
    (key, val), = a.items()
    if key == "upload":
        n = len(val.get("files") or []) if isinstance(val, dict) else 0
        return f"upload {n} file(s) into {_target_label(val)}"
    if key == "dialog":
        answer = val.get("answer") if isinstance(val, dict) else val
        extra = (" +prompt_text"
                 if isinstance(val, dict) and val.get("prompt_text") else "")
        return f"dialog {answer} (armed){extra}"
    if key == "tab":
        if isinstance(val, dict):
            return f"tab open {str(val.get('open', ''))[:60]!r}"
        if isinstance(val, int):
            return f"tab switch to {val}"
        return f"tab {val}"
    if key == "fill":
        return f"fill {_target_label(val)}"
    if key == "hover":
        return f"hover {_target_label(val)}"
    if key == "drag":
        return (f"drag {_target_label(val)} -> "
                f"{_target_label(val.get('to'))}")
    if key == "select":
        opts = val.get("option") if isinstance(val, dict) else None
        if isinstance(opts, list):
            shown = "/".join(str(o) for o in opts[:3])
            shown += "…" if len(opts) > 3 else ""
        else:
            shown = str(opts)
        return f"select {shown!r} in {_target_label(val)}"
    if key == "navigate":
        return f"navigate {val}"
    if key == "pdf":
        where = val.get("save_to") if isinstance(val, dict) else None
        return f"pdf {str(where)[:80]!r}"
    if key == "press":
        target = val.get("selector")
        if target is None and val.get("index") is not None:
            target = f"[{val['index']}]"
        return f"press {val.get('key')!r}" + (f" in {target}" if target else "")
    if key == "scroll":
        return f"scroll x{val.get('steps')}"
    if key == "screenshot":
        # 整页捕获按设计就没有 selector/index，`_target_label` 对这样的 dict 交回
        # repr(None)，于是回执念出一个不存在的目标（"screenshot None"）。submit 为同
        # 一种洞补过并留了注释，这一格漏了：整页就照整页说。
        if isinstance(val, dict) and val.get("full_page"):
            base = "screenshot whole page"
        else:
            base = f"screenshot {_target_label(val)}"
        return base + (
            " + decode_qr" if isinstance(val, dict) and val.get("decode_qr") else "")
    if key == "snapshot":
        return "snapshot interactive elements"
    if key == "read":
        # 回执那一行要说清读了什么、读回来存成了什么名字 —— 后面那条 fill 的
        # `{=名字}` 是从这一行来的，读者不必回头数链。
        #
        # 两层的名字要分开：`@value` 是标签上那一层，`@value (property)` 是页面上
        # 现在那一层。分不开，"我填进去的到底还在不在"就只能靠猜 —— 而这一行的全部
        # 意义就是替人省掉那次猜（细节在 `read_via`，标签给一眼能比的形状）。
        base = f"read {_target_label(val)}"
        if isinstance(val, dict) and val.get("property"):
            return f"{base} @{val['property']} (property)"
        if isinstance(val, dict) and val.get("attribute"):
            base += f" @{val['attribute']}"
        if isinstance(val, dict) and val.get("as"):
            base += f" as {val['as']!r}"
        return base
    if key == "submit":
        # _target_label(val), not val.get("selector"): a submit addressed by
        # snapshot index normalizes to {"index": N} with NO "selector" key, so
        # the old .get("selector") produced "submit None (unconfirmed)" - and
        # that string is what DHOLE_USAGE_LOG's write-audit row recorded, i.e.
        # the audit trail lost the target of the very step it exists to record.
        base = f"submit {_target_label(val)}"
        return base + ("" if isinstance(val, dict) and val.get("confirm")
                       else _UNCONFIRMED)
    if key == "click":
        return f"click {_target_label(val)}"
    if key == "wait_selector":
        sel = val if isinstance(val, str) else (val or {}).get("selector")
        return f"wait_selector {sel!r}"
    return f"{key} {val}"


def _substitute_refs(text: str, values: dict, where: str) -> str:
    """把 `{=名字}` 换成同一条链里那次 read 读回来的文本。

    验证期已经拒过"没登记过的名字"，所以这里还缺一颗名字只有一种意思：**那次 read
    跑了却没读出东西**（它的回执行里带着 error）。此刻不填、并指名去看那一行 ——
    把 `{=code}` 原样交给网站是一次看起来成功的提交，而提交出去的是那六个字符。
    """
    missing = sorted({n for n in _REF_RE.findall(text) if n not in values})
    if missing:
        raise ValueError(
            f"{where} references " + ", ".join("{" + f"={m}" + "}" for m in missing)
            + " but that read produced no value - its row in metadata.actions "
              "carries the reason, and nothing was filled.")
    found = sorted({m.group(1) for m in _REF_RE.finditer(text) if m.group(1) in values})
    out = _REF_RE.sub(lambda m: values[m.group(1)], text)
    if len(out) > MAX_FILL_TEXT_CHARS:
        raise ValueError(
            f"{where} would fill {len(out):,} chars after substituting "
            + " / ".join(f"{{={n}}}" for n in sorted(found))
            + f" (fill.text caps at {MAX_FILL_TEXT_CHARS:,}) - the read it "
            "references is longer than the field can take")
    return out


def _hint_unresolved_braces(text: str, values: dict, shot: dict) -> None:
    """fill 成功后的一句软提示：text 长得像引用、链里却没有那个名字。

    `{=code}` 在验证期已被具名拒绝，而 `{CODE}` 这种**没有等号**的猜法会按字面
    填进表单 —— 那是「plain 花括号是文本」的设计（有测试钉着），但 2026-10-25
    P07 在挑战页上就是猜的这一个（challenge.log：answer=`{CODE}` 被原样 POST，
    回执只说 fill ok）。不改判据，只在这 exactly 一个形状上把正确的形状教出去。
    """
    guessed = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]{0,30})\}", text)))
    guessed = [g for g in guessed if g not in values]
    if not guessed:
        return
    have = (", ".join(sorted(values)) if values
            else "none (no earlier read step named one with as=)")
    shot["note"] = (
        f"'{{{guessed[0]}}}' went in as literal text (no register named "
        f"{guessed[0]}). A register reference is {{=name}} - name is minted by an "
        f"earlier read step's as=. Registers this chain holds: {have}")


def _action_failure(exc: Exception, page=None) -> str:
    """The first useful line of an action failure, in caller-facing terms.

    Playwright's own text is a call log; the part that matters here is which of
    the two things went wrong — nothing matched the selector, or it matched but
    never became actionable.

    R04-001：selector 落空且**这页有子 frame**时，把「顶层没有、可能在 frame
    里」这半句说出去——失败现场拿得到 page，子 frame 数是当场可知的一般事实
    （round-04 P02 实测三步各烧 10s 而错误从头到尾没提 frame:）。页面无 frame
    或失败与 selector 无关时，一个字不加。
    """
    text = str(exc).strip()
    first = text.splitlines()[0][:160] if text else type(exc).__name__
    if "waiting for locator" in text or "no element" in text.lower():
        msg = ("the selector matched no element (or none became actionable) before "
               f"the timeout: {first}")
        if page is not None:
            try:
                child_frames = [f for f in page.frames if f is not page.main_frame]
            except Exception:
                child_frames = []
            if child_frames:
                msg += (f" · this page hosts {len(child_frames)} <iframe> element(s) and "
                        "the selector only sees the TOP document - if the element lives "
                        'inside one, address it with "frame": (the iframe\'s name, a URL '
                        'prefix, or a CSS selector for the <iframe>), e.g. {"selector": '
                        '"...", "frame": "#app"} - sys_guide(topic=\'behavior\') has the '
                        "full shape")
        return msg
    return first


def actions_want_visual(actions) -> bool:
    """True when this chain contains a screenshot action.

    A screenshot is the one action whose product IS the rendered page, so the
    resource blocking that makes a plain fetch fast has to be off for it. The
    stealthy path disables ``image``/``stylesheet``/``font`` for speed
    (``DISABLED_RESOURCE_TYPES``), and an element crop taken under that policy is
    a broken-image placeholder rather than the QR code or CAPTCHA the caller
    asked to look at — measured: with blocking on, the browser never even issued
    ``GET`` for the page's ``<img>``, so ``decode_qr`` answered ``[]`` and the
    action still reported ``ok: true``.

    Scanned on the RAW list with the same key test ``_validate_actions`` uses, so
    it can run before validation and cannot drift from the parser.
    """
    if not isinstance(actions, (list, tuple)):
        return False
    return any(isinstance(a, dict) and "screenshot" in a for a in actions)


def build_page_action(actions) -> Optional[Callable]:
    """Validate `actions` and return an async page_action(page) callable, or None
    if `actions` is None/empty.

    The callable carries `.outcomes`: one row per action, ok or why not. The
    fetch path publishes it into the response (`metadata.actions` + summary),
    because an action that failed quietly used to look like an action that
    happened and did nothing — see _report_action_outcomes in server.py.

    It also carries `.pin_state`, a dict the browser layer writes before running
    the chain (刀9-7 · F9): `{"reused": bool}` = this chain stands on the page a
    previous pinned call left open, `{"pinned": bool}` = this page will be kept
    for the next call. The submit gate reads it: on a page that stays open,
    "re-run the same actions with confirm=true" is not the truth any more - the
    earlier fills already ran and the page has changed since. A dict on the
    callable, not a global, because the browser layer and the action layer must
    not share an invisible fact.
    """
    if not actions:
        return None
    validated = _validate_actions(actions)
    outcomes: list[dict] = []
    pin_state: dict = {}

    async def page_action(page) -> None:
        outcomes.clear()
        # 这一条链的寄存器：`{"read": {..., "as": "code"}}` 把读回来的文本存在这里，
        # 后面的 `fill` 用 `{=code}` 取用。每次导航重新起一条链（`outcomes` 同样清空），
        # 所以下一页的挑战不会带着上一页的值 —— 那一件事正是这条链存在的理由。
        values: dict[str, str] = {}
        # 这一条链站的是不是驻留的那一页（刀9-7 · F9）。浏览器层在调用这条链之前把
        # `pin_state` 写好：`reused` = 站在上一发留下的那一页上，`pinned` = 这一发结束后
        # 这一页会被留下。提交闸要这两件事的或 —— 只要这一页还会被人接着用，"把同一串
        # 动作再跑一遍"就不再是全部的真话。默认（没有写）就是 False，于是提交闸那句合同
        # 与从前逐字相同。
        _pinned_now = bool(pin_state.get("reused") or pin_state.get("pinned"))
        total_steps = len(validated)
        # 页签表：0 号位是这一发导航/复用的那一页；tab 动作往里追加/摘除，`page`
        # 的重绑跟着走。收尾（finally）把 1..N 号位写成 `tab_pages`——浏览器层靠
        # 这一格决定"多出来的页签留进驻留登记表还是关掉"。
        chain_pages: list = [page]

        async def _run_chain(page, _listen_dialogs) -> None:
            # 页签表住在 page_action 作用域（`chain_pages`）：0 号位是这一发导航/
            # 复用的那一页，1..N 是链里开的（或上一次钉住留下来的）。`page` 这个
            # 名字跟着活动页走——tab 分支重绑它，后面的每一步就都站在新活动页上；
            # 快照的帧表、对话框监听器按页各归各。收尾的 tab_pages 记账在
            # page_action 的 finally（链被预算掐断时也要交）。
            for p in (pin_state.get("prior_tabs") or []):
                try:
                    alive = not p.is_closed()
                except Exception:
                    alive = False
                if alive and not any(p is q for q in chain_pages):
                    chain_pages.append(p)
            _listen_dialogs(page)
            entry_url = str(pin_state.get("entry_url") or "")
            # 动作链是"几十秒到几分钟"的另一种形状：一步 click 可以等 settle，一步
            # wait 是显式的秒数。每**要执行**的一步报一次（在动作之前报，因为被
            # submit 闸拦下的那一步不该被说成做过）；链里 continue 的分支因此也带
            # 着自己的那一格。
            for step_no, a in enumerate(validated, start=1):
                label = action_label(a)
                await report_progress(float(step_no - 1), total=float(total_steps),
                                      message=f"action {step_no}/{total_steps}: {label}")
                shot: dict = {}
                try:
                    if "click" in a:
                        loc = await _target_locator(page, a["click"])
                        submits = await _is_submit_control(loc)
                        if submits:
                            # 落在提交型控件上的 click 就是提交（评审R1刀1）：闸认
                            # 动作效果，不认动作名。confirm 落在 click 的 dict 上
                            # （字符串简写不带键，pending 行教它怎么改写）。
                            click_val = a["click"]
                            confirm = (click_val.get("confirm", False)
                                       if isinstance(click_val, dict) else False)
                            rerun = ("on this click" if isinstance(click_val, dict)
                                     else 'on this click (as {"click": '
                                          '{"selector": ..., "confirm": true}})')
                            row = await _submit_gate_row(label, confirm, rerun,
                                                         pinned=_pinned_now)
                            if row is not None:
                                outcomes.append(row)
                                continue
                            evidence = await _settle_after_submit(page, loc)
                            if not evidence:
                                outcomes.append(await _no_submission_row(label, loc))
                                continue
                            shot["submitted"] = True
                            shot["submission"] = "+".join(evidence)
                        else:
                            await _settle_after_click(page, loc)
                    elif "hover" in a:
                        loc = await _target_locator(page, a["hover"])
                        await loc.hover(timeout=_step_deadline_ms())
                        await _settle_quiet(page)
                    elif "drag" in a:
                        d = a["drag"]
                        src = await _target_locator(page, d)
                        dst = await _target_locator(page, d["to"])
                        await src.drag_to(dst, timeout=_step_deadline_ms())
                        await _settle_quiet(page)
                    elif "upload" in a:
                        u = a["upload"]
                        loc = await _target_locator(page, u)
                        await loc.set_input_files(u["files"],
                                                  timeout=_step_deadline_ms())
                        shot["uploaded"] = [os.path.basename(p) for p in u["files"]]
                        shot["files"] = len(u["files"])
                        # R08-002：文件是页面态，活到这一发导航为止——跨调用提交会
                        # 拿到一只空 input，落成 `received (0 bytes)` 还看着像成功。
                        # 这半句在装文件的现场说，不在空结果现场猜。
                        shot["lifetime"] = ("the files ride THIS page instance - "
                                            "submit within the same call (a later "
                                            "call starts a fresh page)")
                    elif "dialog" in a:
                        d = a["dialog"]
                        armed.append(d)
                        shot["dialog_armed"] = d["answer"]
                    elif "tab" in a:
                        if pin_state.get("attach"):
                            raise ValueError(
                                "tab actions are refused on a CDP-attached "
                                "session - those tabs live in the user's own "
                                "browser; use a dhole session (session_id=) "
                                "for multi-tab work")
                        spec = a["tab"]
                        if spec == "new" or (isinstance(spec, dict)
                                             and "open" in spec):
                            open_url = (str(spec.get("open") or "")
                                        if isinstance(spec, dict) else "")
                            newp = await page.context.new_page()
                            chain_pages.append(newp)
                            _listen_dialogs(newp)
                            if open_url:
                                try:
                                    _nav = await newp.goto(
                                        open_url, wait_until="load",
                                        timeout=_step_deadline_ms())
                                    # 这一页自己的导航响应跟着页走：fetch 落账
                                    # 时读它（`_dhole_nav_response`），状态行/
                                    # 响应头说的是这一页的这一笔，不是入口页的。
                                    newp._dhole_nav_response = _nav
                                except Exception as exc:
                                    with contextlib.suppress(Exception):
                                        await newp.close()
                                    chain_pages[:] = [q for q in chain_pages
                                                      if q is not newp]
                                    raise ValueError(
                                        "tab open failed for "
                                        f"{open_url!r}: {_action_failure(exc)}"
                                    ) from None
                                from dhole_mcp.browser import _landed_url_refused
                                if entry_url and _landed_url_refused(
                                        newp.url, entry_url):
                                    with contextlib.suppress(Exception):
                                        await newp.close()
                                    chain_pages[:] = [q for q in chain_pages
                                                      if q is not newp]
                                    raise ValueError(
                                        "tab open refused: the new tab landed "
                                        "at an internal/private address instead "
                                        f"of the entry site ({open_url!r}) - "
                                        "nothing was read from it")
                            page = newp
                            pin_state["active_page"] = page
                            shot["tab_opened"] = len(chain_pages) - 1
                            shot["url"] = page.url
                        elif isinstance(spec, int):
                            if not 0 <= spec < len(chain_pages):
                                raise ValueError(
                                    f"tab {spec} does not exist - inventory: "
                                    f"{[q.url for q in chain_pages]}")
                            page = chain_pages[spec]
                            pin_state["active_page"] = page
                            _listen_dialogs(page)
                            shot["tab_active"] = spec
                            shot["url"] = page.url
                        elif spec == "close":
                            if len(chain_pages) == 1:
                                raise ValueError(
                                    "refusing to close the only tab - the chain "
                                    "still needs a page to answer from; use "
                                    '{"tab": "new"} first')
                            idx = next(i for i, q in enumerate(chain_pages)
                                       if q is page)
                            with contextlib.suppress(Exception):
                                await page.close()
                            chain_pages.pop(idx)
                            page = chain_pages[0]
                            pin_state["active_page"] = page
                            shot["tab_closed"] = idx
                            shot["tab_active_now"] = 0
                            shot["url"] = page.url
                        elif spec == "list":
                            shot["tabs"] = [{"index": i, "url": q.url}
                                            for i, q in enumerate(chain_pages)]
                    elif "navigate" in a:
                        nav = a["navigate"]
                        # wait_until="commit"：back/forward 常落进 SPA 的路由
                        # （popstate，没有真正的加载事件），等 load 会白等到超时
                        # ——真机实测（2026-10-25，靶站双页）：默认档在历史导航上
                        # 挂满 30s 超时，commit 两向都是落定即回。这条驱动的历史
                        # 导航**即使真的动了也解析成 None**，所以"动没动"不问
                        # 返回值，问 URL：变了，新地址自己说话；没变，交回
                        # page_unchanged——是"历史里没有那一格"还是"同文档入口
                        # 没改变什么"从这里分辨不出，不替页面作证。
                        was = page.url
                        await (page.go_back(wait_until="commit",
                                            timeout=_step_deadline_ms())
                               if nav == "back" else
                               page.go_forward(wait_until="commit",
                                               timeout=_step_deadline_ms()))
                        await _settle_quiet(page)
                        shot["navigate"] = nav
                        shot["url"] = page.url
                        if page.url == was:
                            shot["page_unchanged"] = True
                    elif "fill" in a:
                        f = a["fill"]
                        loc = await _target_locator(page, f)
                        await loc.fill(_substitute_refs(f["text"], values, label),
                                       timeout=_step_deadline_ms())
                        _hint_unresolved_braces(f["text"], values, shot)
                    elif "select" in a:
                        s = a["select"]
                        loc = await _target_locator(page, s)
                        got = await loc.evaluate(_SELECT_RESOLVE_JS, s["option"])
                        if not isinstance(got, dict):
                            got = {}
                        tag = str(got.get("tag") or "")
                        if tag != "select":
                            raise ValueError(
                                f"select found <{tag or 'unknown'}> there - "
                                "select is for <select> dropdowns; an input or "
                                "textarea takes {\"fill\": ...} instead")
                        resolved = [r for r in (got.get("resolved") or [])]
                        misses = [w for w, r in zip(s["option"], resolved)
                                  if not r]
                        if misses:
                            have = "; ".join(
                                (f"{lab}={val}" if val and val != lab else str(lab))
                                for lab, val in (got.get("options") or [])) \
                                or "no <option> elements at all"
                            raise ValueError(
                                f"no option matches {misses[0]!r} on this "
                                f"select - what it holds: {have}")
                        if len(resolved) > 1 and not got.get("multiple"):
                            raise ValueError(
                                "this <select> takes ONE option - a list is for "
                                "a multi-select; pass option as a single string")
                        await loc.select_option(
                            value=[r[0] for r in resolved],
                            timeout=_step_deadline_ms())
                        # 级联下拉（省 → 市）在 change 之后才填下一级：与 hover/drag
                        # 同款，等 DOM 安静，不赌站点同步渲染。
                        await _settle_quiet(page)
                        shot["selected"] = [
                            {"value": r[0], "label": str(r[1] or "")[:60]}
                            for r in resolved]
                    elif "press" in a:
                        p = a["press"]
                        if p.get("index") is not None:
                            loc = await _target_locator(page, p)
                            await loc.press(p["key"], timeout=_step_deadline_ms())
                        elif p.get("selector"):
                            await page.locator(p["selector"]).first.press(
                                p["key"], timeout=_step_deadline_ms())
                        else:
                            await page.keyboard.press(p["key"])
                    elif "wait" in a:
                        await page.wait_for_timeout(a["wait"])
                    elif "scroll" in a:
                        s = a["scroll"]
                        await _scroll(page, s["steps"], s["selector"], s["ms_per_step"])
                    elif "wait_selector" in a:
                        w = a["wait_selector"]
                        if w["count"] > 1:
                            # "N items exist" is the lazy-list question; the first
                            # match has been on the page since it rendered.
                            await page.wait_for_function(
                                _COUNT_JS, arg=[w["selector"], w["count"]],
                                timeout=_step_deadline_ms(w["timeout"]))
                        else:
                            await page.locator(w["selector"]).first.wait_for(
                                state=w["state"], timeout=_step_deadline_ms(w["timeout"]))
                    elif "screenshot" in a:
                        import base64
                        s = a["screenshot"]
                        full_page = s.get("full_page")
                        image_type = s.get("image_type", "png")
                        save_target = None
                        if s.get("save_to"):
                            # 同 web_screenshot 时代：坏路径在快门前一刻也值一次拒绝，
                            # 但此刻浏览器已经起来了——至少让它在写盘前一刻而不是
                            # 之后才炸。归一化时已验过形状，这里只落盘。
                            save_target = _resolve_save_to(s["save_to"], image_type)
                        shot_kwargs: dict = {"type": image_type, "timeout": _step_deadline_ms()}
                        if s.get("quality") is not None:
                            shot_kwargs["quality"] = s["quality"]
                        if full_page:
                            # 整页捕获没有元素盒子可依：page.screenshot 接 full_page。
                            try:
                                data = await page.screenshot(full_page=True,
                                                             **shot_kwargs)
                            except Exception as exc:
                                logger.debug("capture failed: %r", exc)
                                # 净化器把驱动方的 "Call log" 掉段砍掉，换成能改的
                                # 那个旋钮的名字（timeout / 会话 / 二进制）——与
                                # web_screenshot 时代同一张映射表，只是家搬了。
                                raise RuntimeError(
                                    _screenshot_error_message(exc)) from None
                        else:
                            if s.get("index") is not None:
                                loc = await _target_locator(page, s)
                            else:
                                loc = await _locator_or_raise(page, s["selector"])
                            try:
                                data = await loc.screenshot(**shot_kwargs)
                            except Exception as exc:
                                logger.debug("capture failed: %r", exc)
                                raise RuntimeError(
                                    _screenshot_error_message(exc)) from None
                        if save_target is not None:
                            # save_to 是 text-only 宿主的整条出路：字节走盘，路径进
                            # 回执，inline 一张都不发——大图也不再受 inline 帽约束。
                            saved = _write_screenshot(data, save_target)
                            shot["saved"] = saved
                            shot["saved_bytes"] = len(data)
                        else:
                            if len(data) > MAX_ACTION_SHOT_BYTES:
                                raise ValueError(
                                    f"capture is {len(data):,} bytes "
                                    f"(cap {MAX_ACTION_SHOT_BYTES:,} for inline "
                                    "images) - crop a smaller element, or pass "
                                    '{"save_to": "..."} to write the full capture '
                                    "to disk instead of the transcript")
                            shot["image_b64"] = base64.b64encode(data).decode("ascii")
                            shot["image_mime"] = f"image/{image_type}"
                        if s["decode_qr"]:
                            # Auxiliary to the capture: a decoder problem (missing
                            # extra, no code in the crop) reports in the row and
                            # never fails the action — the pixels are the product.
                            try:
                                from dhole_mcp.qr import decode_qr_bytes
                                shot["qr"] = decode_qr_bytes(data)
                            except Exception as e:
                                shot["qr"] = []
                                shot["qr_error"] = f"{type(e).__name__}: {str(e)[:140]}"
                    elif "pdf" in a:
                        p = a["pdf"]
                        # 打印发生在链的这一步：先前动作（登录、翻页、展开）都已
                        # 体现在这份 DOM 上。print_background 开着——深色主题与
                        # 高亮行承载信息，背景不是装饰。
                        target = _resolve_pdf_save_to(p["save_to"])
                        try:
                            # 不传 timeout：Python 绑定的 page.pdf() 没有这颗参数
                            # （真机实测 TypeError），打印本身由驱动自己收尾。
                            data = await page.pdf(print_background=True)
                        except Exception as exc:
                            if "headless" in str(exc).lower():
                                raise ValueError(
                                    "printing to PDF needs the hidden browser - "
                                    "this chain runs in a VISIBLE window "
                                    "(headed=true) or an attached Chrome "
                                    "(cdp_url=), neither can print; re-run the "
                                    "capture without them") from None
                            first = (str(exc).splitlines()[0][:160]
                                     if str(exc).strip() else type(exc).__name__)
                            raise RuntimeError(
                                f"{type(exc).__name__}: {first}") from None
                        saved = _write_screenshot(data, target)
                        shot["saved"] = saved
                        shot["saved_bytes"] = len(data)
                    elif "read" in a:
                        r = a["read"]
                        loc = await _target_locator(page, r)
                        attr = r.get("attribute")
                        prop = r.get("property")
                        if prop:
                            raw = await loc.evaluate(_READ_PROPERTY_JS, prop)
                            if raw is None:
                                tag = await loc.evaluate(_TAG_JS)
                                raise ValueError(
                                    f"read found no property {prop!r}: <{tag.lower()}> "
                                    f"has no such field — it is not a form control, so "
                                    'there is no live value to read')
                            text = str(raw)
                            what = f"property {prop!r}"
                            read_via = f"property:{prop}"
                        elif attr:
                            if attr in _VALUEISH_ATTRS:
                                tag = await loc.evaluate(_TAG_JS)
                            else:
                                tag = ""
                            if tag in _VALUEISH_TAGS:
                                # 问的是 attribute，答的是 property —— 并且把标签上那一层
                                # 一起带回去。两个数不等就是"填进去的那些字此刻在页面上"
                                # 的凭据；相等则说明这次读分辨不了（值本来就等于默认）。
                                # 这一行的意义在于：它让"按下提交之前我到底填没填上"
                                # 成为一次调用就能回答的问题，而不是四次。
                                live = await loc.evaluate(_READ_PROPERTY_JS, attr)
                                if live is None:
                                    raise ValueError(
                                        f"read found no property {attr!r} on <"
                                        f"{tag.lower()}> {attr!r} attribute read")
                                text = str(live)
                                shadow = await loc.get_attribute(attr)
                                read_via = f"property:{attr} (asked as attribute)"
                                if (shadow or "") != text:
                                    shot["attribute_says"] = "" if shadow is None else str(shadow)
                                what = f"attribute {attr!r}"
                            else:
                                raw = await loc.get_attribute(attr)
                                text = "" if raw is None else str(raw)
                                what = f"attribute {attr!r}"
                                read_via = f"get_attribute:{attr}"
                            empty_hint = (
                                f"{attr!r} is absent or empty on this element"
                                if tag not in _VALUEISH_TAGS else
                                f"{attr!r} is empty on this <{tag.lower()}> — that is the "
                                "field's current value, not a missing read")
                        else:
                            text = await loc.inner_text(timeout=_step_deadline_ms())
                            what = "text"
                            read_via = "inner_text"
                            # 空读数最常被读成"这一页没有答案"，而真相通常是读错了地方：
                            # 表单框的值不是元素的文本，图里的挑战更不是。这两扇门都点名，
                            # 否则这一行只是把"没读到"原样交回给人猜。
                            #
                            # 2026-10-10：这里原先指的是 `{"attribute": "value"}` —— 那一读
                            # 拿到的是标签上的死值，正是它把一次成功的 fill 说成失败的。
                            # 指向错的门比不指门更糟：调用方会照着它信一个假证据。
                            empty_hint = (
                                "a form field's live value needs {\"property\": \"value\"}"
                                "; a challenge drawn as an image needs a screenshot action")
                        text = text.strip()
                        if not text:
                            raise ValueError(f"read found no {what}: {empty_hint}")
                        shot["read_via"] = read_via
                        if len(text) > MAX_ACTION_READ_CHARS:
                            shot["read"] = text[:MAX_ACTION_READ_CHARS]
                            shot["read_truncated"] = True
                            shot["read_chars_total"] = len(text)
                        else:
                            shot["read"] = text
                        if r.get("as"):
                            # 寄存器存**全文**：帽是说给读这一行的人听的，填回表单的那一半
                            # 不该拿到一枚被砍过的答案。
                            if r["as"] in values:
                                # R08-003：重铸同名是合法模式（刷新型重读），last-wins 保留；
                                # 但覆盖要出声——fill 实填的值就此换人，调用方不能靠猜。
                                shot["register_overwritten"] = (
                                    f"'{r['as']}' was already minted by an earlier read "
                                    f"in this chain ({values[r['as']]!r}) - this read "
                                    f"replaced it; later {{={r['as']}}} uses THIS value")
                            values[r["as"]] = text
                            shot["read_as"] = r["as"]
                    elif "snapshot" in a:
                        rows, total, truncated = await snapshot_elements(page)
                        if not total:
                            raise ValueError(
                                "could not enumerate interactive elements - the "
                                "document may still be loading; add "
                                '{"wait_selector": ...} before the snapshot')
                        shot["elements"] = [
                            {"index": r[0], "tag": r[1], "role": r[2], "name": r[3],
                             **({"disabled": True} if r[4] else {}),
                             **({"input_type": r[5]} if r[5] else {}),
                             **({"frame": r[6]} if len(r) > 6 and r[6] else {})}
                            for r in rows if isinstance(r, list) and len(r) >= 6]
                        shot["elements_total"] = total
                        if truncated:
                            # 截断要如实说：agent 以为看到全部才会把"没有"当成"不存在"。
                            shot["elements_truncated"] = True
                    elif "submit" in a:
                        s = a["submit"]
                        # 审批闸（安全红线 3）：提交是不可逆的一步。批次8 刀1 把
                        # 钥匙按连接分段：声明了 elicitation 的连接上，人按键授权
                        # （out-of-band ask）——confirm 参数不参与开门，拒答 / 问
                        # 不到人一律 fail-closed；能力为假 / 无会话 → 一字不改，
                        # {"confirm": true} 重跑同一条 actions 链照旧放行。闸体在
                        # _submit_gate_row：评审R1刀1 起，提交型控件上的 click 走
                        # 同一道门。
                        #
                        # 闸**排在解析之前**，这一条有专门的钉子守着
                        # （`test_a_submit_that_went_out_is_not_marked_unconfirmed.py
                        # ::test_a_refused_submit_never_reaches_the_locator`）：被拦下
                        # 的一步不许去问页面任何问题。我曾把它挪到解析之后（越界的
                        # index 不该让人去批准一件办不成的事 —— 实测 S12/S15 那句
                        # pending 原样回显 [7]），撞在那颗钉子上让位了：钉子说的是
                        # 安全侧，我说的是省一次往返。
                        row = await _submit_gate_row(label, s.get("confirm", False),
                                                     "on this submit",
                                                     pinned=_pinned_now)
                        if row is not None:
                            outcomes.append(row)
                            continue
                        label = _permitted(label)
                        loc = await _target_locator(page, s)
                        evidence = await _settle_after_submit(page, loc)
                        if not evidence:
                            outcomes.append(await _no_submission_row(label, loc))
                            continue
                        shot["submitted"] = True
                        shot["submission"] = "+".join(evidence)
                    outcomes.append({"action": label, "ok": True, **shot})
                except asyncio.CancelledError:
                    # 调用预算掐死这一步的时候，链到这里就结束了 —— 而下面那条
                    # `except Exception` 接不到它（CancelledError 自 3.8 起不再是
                    # Exception 的子类，它从 BaseException 来）。后果是这一格交回
                    # "什么都没有"：8 步跑完 6 步和一步没跑，在字节上长得一模一样。
                    #
                    # 实测的形状（15 场景体测，场景 10）：一次元素截图烧掉 61,529ms，
                    # 响应写着 "No content was extracted"，`metadata.actions` 一个字都
                    # 没有 —— 于是连"这一刀切在哪一步"都无从知道，而那正是决定要不要再
                    # 来一次的唯一依据。
                    #
                    # 这一刀不改任何截止：它只让被掐掉的那一步留下自己的一行，然后原样
                    # 交回，让外面那道 backstop 继续建它的超时响应。
                    outcomes.append({
                        "action": label, "ok": False, "cut_by_budget": True,
                        "step": f"{step_no}/{total_steps}",
                        "error": ("cut by the caller's own timeout - this step never "
                                  "finished, so whatever it changes on the page may be "
                                  "half-done"),
                    })
                    raise
                except Exception as e:
                    outcomes.append({"action": label, "ok": False,
                                     "error": _action_failure(e, page=page)})
                    # `label`, never `a`: a fill action's dict carries its `text`,
                    # and that text can be a password (the login flow builds exactly
                    # such a chain). Logging the raw action wrote the secret to the
                    # log while the response, the receipt and the audit row were all
                    # careful not to - the one channel nobody audits was the leak.
                    # The label names the target and never the value, so it is what
                    # every other consumer of this action already records.
                    logger.warning("page action %s failed: %s", label, str(e)[:160])

        # ── 对话框账本（dialog 动作的另一半）───────────────────────────
        # 监听器挂在**链首**：Playwright 的默认是「没人监听就自动 dismiss」，安全侧
        # 正确，但它静默——agent 点了"删除"却什么都没发生，连为什么都没人说。装上
        # 监听器之后，这一条链上弹的每一个对话框都从这里走：有人武装（{"dialog":
        # "accept"/"dismiss"} 先行）就照武装的答，没人武装就照旧 dismiss——**并把它
        # 记下来**。链尾多交一行 "dialogs"，弹没弹、弹的什么、谁答的，一眼可查。
        dialogs: list[dict] = []
        armed: list[dict] = []

        async def _on_dialog(dlg) -> None:
            entry: dict = {"type": str(getattr(dlg, "type", "") or ""),
                           "message": str(getattr(dlg, "message", "") or "")[:160]}
            answer = armed.pop(0) if armed else None
            accepted = answer is not None
            try:
                if accepted and answer.get("answer") == "dismiss":
                    await dlg.dismiss()
                elif accepted and answer.get("prompt_text") is not None \
                        and entry["type"] == "prompt":
                    await dlg.accept(answer["prompt_text"])
                elif accepted:
                    await dlg.accept()
                else:
                    await dlg.dismiss()
            except Exception:
                accepted = False
                with contextlib.suppress(Exception):
                    await dlg.dismiss()
            _was_dismiss = accepted and answer.get("answer") == "dismiss"
            entry["answered"] = ("dismissed (armed)" if _was_dismiss
                                 else "accepted (armed)" if accepted
                                 else "auto-dismissed")
            if answer is not None and not accepted:
                entry["arm_failed"] = ("the armed answer itself raised - the "
                                       "dialog was dismissed instead")
            if answer is None:
                entry["note"] = ('no {"dialog": "accept"} was armed before the '
                                 "action that popped this - arm one to answer it")
            dialogs.append(entry)

        _dialog_pages: list = []

        def _listen_dialogs(p) -> None:
            """把对话框账本挂到一页上。tab 动作切页后新活动页也要挂——否则切过去
            之后弹的 confirm 又回到"静默被关"的旧世界。有借有还：page_action 的
            finally 对 `_dialog_pages` 里每一页摘监听器。"""
            try:
                p.on("dialog", _on_dialog)
                _dialog_pages.append(p)
            except Exception:
                pass

        try:
            await _run_chain(page, _listen_dialogs)
        finally:
            # 全表如实交出去（含 0 号位与活动页）：浏览器层存槽时自己排除活动页、
            # 收尾时自己排除正在关的那一页。切过页的链里 0 号位（导航那页）也要交
            # ——不交它就成了没人管也没人记的漏页。
            pin_state["tab_pages"] = list(chain_pages)
            for p in _dialog_pages:
                with contextlib.suppress(Exception):
                    p.remove_listener("dialog", _on_dialog)
            if armed:
                dialogs.append({"armed_unused": len(armed),
                                "note": "armed answer(s) that never met a dialog"})
            if dialogs:
                outcomes.append({"action": "dialogs", "ok": True,
                                 "seen": dialogs})
    page_action.outcomes = outcomes  # type: ignore[attr-defined]
    # 驻留页的告示牌（见上面 docstring）：浏览器层在跑这条链之前往里写 `reused`，
    # 提交闸读它。默认缺席 = 这一页是这一发导航出来的，合同照旧。
    page_action.pin_state = pin_state  # type: ignore[attr-defined]
    # 链一共要走几步。`len(outcomes)` 只说得出记了几行，说不出被掐断的那条链本来
    # 还有几步没走 —— 而"切在第几步"是读的人唯一关心的那件事。
    page_action.total_steps = len(validated)  # type: ignore[attr-defined]
    return page_action
