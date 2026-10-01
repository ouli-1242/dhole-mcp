"""Page interaction for smart_fetch (the `actions` param).

When the agent passes `actions=[...]`, smart_fetch forces the stealthy browser
tier and runs the actions on the page after navigation, before content
extraction. This reaches content behind a click, a search form, a "load more"
button, or infinite scroll — cases a plain fetch can't.

Implementation: patchright's stealthy fetch accepts a `page_action` callable that
receives the Playwright AsyncPage after goto and is awaited. We build that
callable from a validated list of action dicts and thread it through
smart_fetch -> _force_fetch -> stealthy_fetch -> session.fetch(page_action=...).

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
  {"snapshot": true}                        numbered interactive-element list
                                            (index/tag/role/name) for the agent
                                            to act on by index — no CSS needed
  {"click": {"index": 17}}                  act by INDEX (from a {"snapshot"} row);
  {"click": {"index": 17, "expect": "登录"}}  `expect` verifies the element's name
                                            before acting (a stale index must
                                            fail loudly, not click the wrong
                                            thing). `expect` also works with a
                                            selector.

Validation is strict (CSS selectors validated, counts/ms capped) so a bad action
fails fast instead of hanging the browser. Per-action errors are caught so one
failing step doesn't abort the rest; the agent gets the page in whatever state
it reached.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Callable, Optional

from dhole_mcp import env_registry

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
_VALID_KEYS = {"click", "fill", "press", "wait", "scroll", "wait_selector",
               "screenshot", "snapshot", "submit"}

# An element capture only exists to be LOOKED at, so it is the one action gated
# behind DHOLE_MULTIMODAL (=1 declares "the calling agent is multimodal" — the
# recognition happens agent-side; dhole ships no vision model). Kept here rather
# than imported from server.py: server imports actions lazily, and a reverse
# import would drag the whole server module into every lean process that touches
# the actions list.
#
# W4.3: the off/on token lists live in ``env_registry`` so a value that
# ``server._env_flag`` would recognise reads the same here, and an
# unrecognised value gets a warning the doctor can name.
def multimodal_enabled() -> bool:
    return env_registry.read_flag("DHOLE_MULTIMODAL")


def multimodal_gate_error() -> str:
    return (
        "element_screenshot_disabled: the {\"screenshot\"} action is refused "
        "because DHOLE_MULTIMODAL is not set - element captures exist to be "
        "read by a multimodal agent, and without the flag dhole must assume "
        "the caller cannot see images. Set DHOLE_MULTIMODAL=1 in the dhole "
        "server env and restart (it declares the calling agent is multimodal; "
        "dhole itself ships no vision model). Text-only agents: QR codes are "
        "NOT gated - use the screenshot tool with options.decode_qr=true."
    )

# Element captures ride the same page instance as the surrounding fill/click
# actions — that is the whole point over a separate screenshot call: the
# captcha read in this capture is the one the next action answers, on the page
# load that is still alive. A capture this large is a mistake (the whole page,
# not a cropped element), and shipping it spends a multimodal client's context.
MAX_ACTION_SHOT_BYTES = 2_000_000

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

_ENUM_JS = """(sel) => {
  const els = document.querySelectorAll(sel);
  const out = [];
  for (let i = 0; i < els.length && i < """ + str(MAX_ELEMENT_INDEX) + """; i++) {
    const el = els[i];
    const tag = el.tagName.toLowerCase();
    let role = el.getAttribute('role') || '';
    if (!role) {
      const implicit = {a: 'link', button: 'button', select: 'combobox',
                        textarea: 'textbox', option: 'option', input: 'textbox'};
      role = implicit[tag] || '';
      if (tag === 'input') {
        const t = (el.type || 'text').toLowerCase();
        const byType = {checkbox: 'checkbox', radio: 'radio', button: 'button',
                        submit: 'button', file: 'button', range: 'slider',
                        number: 'spinbutton', search: 'searchbox'};
        role = byType[t] || 'textbox';
      }
    }
    let name = el.getAttribute('aria-label') || '';
    if (!name) name = (el.innerText || '').trim().slice(0, 80);
    if (!name && el.value !== undefined && el.value !== null)
      name = String(el.value).slice(0, 80);
    if (!name) name = el.placeholder || el.title || el.alt || el.name
                      || el.id || '';
    out.push([i, tag, role, String(name).trim().slice(0, 80),
              !!el.disabled || el.getAttribute('aria-disabled') === 'true',
              tag === 'input' ? String(el.type || 'text') : '']);
  }
  return [out, els.length];
}"""

# act 时核对元素可访问名（`expect` 的实现）：索引在两次页面加载之间可能指到
# 不同的元素——核对名字让"过期的索引"大声失败，而不是点到别的东西。
_NAME_JS = """(el) => {
  let name = el.getAttribute('aria-label') || '';
  if (!name) name = (el.innerText || '').trim().slice(0, 120);
  if (!name && el.value !== undefined && el.value !== null)
    name = String(el.value).slice(0, 120);
  if (!name) name = el.placeholder || el.title || el.alt || el.name
                    || el.id || '';
  return String(name).trim();
}"""

_COUNT_JS_INTERACTIVE = "(sel) => document.querySelectorAll(sel).length"


async def snapshot_elements(page) -> tuple:
    """枚举当前页面的可交互元素。返回 (rows, total, truncated)。

    rows 是 [index, tag, role, name, disabled, input_type] 的列表（截断到
    SNAPSHOT_CAP），total 是页面上的全量数——截断要如实说，不能让 agent 以为
    看到了全部。
    """
    try:
        got = await page.evaluate(_ENUM_JS, INTERACTIVE_SELECTOR)
    except Exception:
        return [], 0, False
    rows, total = (got if isinstance(got, list) and len(got) == 2 else ([], 0))
    if not isinstance(rows, list):
        rows, total = [], 0
    shown = rows[:SNAPSHOT_CAP]
    # 截断在截取之后判定：JS 侧的上限是 MAX_ELEMENT_INDEX（防失控页），这里的
    # 上限是 SNAPSHOT_CAP（防上下文爆炸）——total 超过"实际展示数"才是截断。
    truncated = int(total or 0) > len(shown)
    return shown, int(total or 0), truncated


async def resolve_index(page, index: int):
    """索引 → Playwright locator（与快照同一选择器、同一文档序）。"""
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


def _act_target(i: int, key: str, val) -> dict:
    """Normalize a click/fill/press/submit/screenshot target to exactly one of:

    ``{"selector": "css"}`` or ``{"index": N}``, plus the optional ``expect``
    name-check. Both spellings address the same element set — the CSS selector
    (the original path) and the snapshot index (MVP-2, "用户不用懂" via the
    outer agent) — and a dict carrying both is a typo, not a combination.
    """
    from dhole_mcp.security import validate_css_selector

    expect = ""
    if isinstance(val, str):
        if not val.strip():
            raise ValueError(f"action {i} {key!r} must be a non-empty CSS selector")
        validate_css_selector(val)
        return {"selector": val}
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
    return target


def _validate_actions(actions) -> list[dict]:
    """Validate + normalize the actions list. Raises ValueError on bad input."""
    from dhole_mcp.security import validate_css_selector

    if not isinstance(actions, list) or not actions:
        raise ValueError("actions must be a non-empty list of action dicts")
    if len(actions) > MAX_ACTIONS:
        raise ValueError(f"Too many actions ({len(actions)}). Maximum is {MAX_ACTIONS}.")
    out: list[dict] = []
    for i, a in enumerate(actions):
        if not isinstance(a, dict) or len(a) != 1:
            raise ValueError(f"action {i} must be a dict with exactly one key {sorted(_VALID_KEYS)}")
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
                out.append({key: _act_target(i, key, val)})
        elif key == "snapshot":
            # {"snapshot": true} — 编号交互元素列表。无参数形态是刻意的：快照
            # 没有可调的东西（上限是保护，不是旋钮），多一个键就多一种拼错法。
            if val is not True:
                raise ValueError(
                    f"action {i} 'snapshot' must be true - usage: "
                    '{{"snapshot": true}}. The element list arrives in '
                    "metadata.actions as a receipt row; act on it with "
                    '{"click": {"index": N}}')
            out.append({key: True})
        elif key == "submit":
            # {"submit": {"selector"|"index", "confirm"?: true}} — 显式提交动作。
            # confirm 缺省 = 执行时暂停并返回 pending_approval（破坏性操作必须
            # 经用户确认，安全红线 3）；confirm=true = agent 已向用户转达并拿到
            # 确认后才该传的值。
            if not isinstance(val, dict):
                raise ValueError(
                    f"action {i} 'submit' must be {{selector, confirm?}} or "
                    "{{index, confirm?}} - a submit is an irreversible step and "
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
            # String shorthand = selector only; the dict adds decode_qr and the
            # snapshot-index alternative. The gate is checked HERE (validation
            # time), so a refused capture costs an instant error instead of a
            # browser cold start.
            if isinstance(val, str):
                spec: dict = {"selector": val}
            elif isinstance(val, dict):
                spec = dict(val)
            else:
                raise ValueError(
                    f"action {i} 'screenshot' must be a CSS selector string or "
                    "{{selector, decode_qr}}")
            want_qr = spec.pop("decode_qr", False)
            if not isinstance(want_qr, bool):
                raise ValueError(f"action {i} 'screenshot.decode_qr' must be a boolean")
            if not multimodal_enabled():
                raise ValueError(multimodal_gate_error())
            out.append({"screenshot": {**_act_target(i, key, spec),
                                       "decode_qr": want_qr}})
        elif key == "fill":
            if not isinstance(val, dict) or "text" not in val:
                raise ValueError(f"action {i} 'fill' must be {{selector, text}} "
                                 "or {{index, text}}")
            text = val["text"]
            if not isinstance(text, str):
                raise ValueError(f"action {i} 'fill.text' must be a string")
            if len(text) > 5000:
                raise ValueError(f"action {i} 'fill.text' too long (max 5000 chars)")
            target = _act_target(
                i, key, {k: v for k, v in val.items() if k != "text"})
            out.append({key: {**target, "text": text}})
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
            out.append({"scroll": {
                "steps": _int_arg(steps, f"action {i} 'scroll'", "steps", MAX_SCROLL),
                "selector": sel or None,
                "ms_per_step": max(250, min(int(ms), SCROLL_STEP_MS_MAX)),
            }})
    return out


_WAIT_STATES = frozenset({"attached", "visible", "hidden", "detached"})


# Public alias for record-time validation. A recipe (tasks.py, L3) has to be
# validated by the SAME validator the executor uses: a stored chain that the
# executor would refuse is a recipe that can never succeed, and the caller who
# saved it has no way to tell — which is worse than no recipe at all. Exposed as
# a name rather than reaching across modules for the underscore version.
validate_actions = _validate_actions


def _int_arg(val, where: str, unit: str, cap: int) -> int:
    """Coerce an int-or-digit-string action argument into a clamped int."""
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
    return max(0, min(n, cap))


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
    if "index" in target and target.get("index") is not None:
        if target.get("expect"):
            await _check_expect(page, target["index"], target["expect"])
        return await resolve_index(page, target["index"])
    sel = target.get("selector")
    if not sel:
        raise ValueError(f"action target has neither selector nor index: {target!r}")
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
    await click_locator.click(timeout=10_000)
    started = time.monotonic()
    deadline = started + CLICK_SETTLE_MS / 1000.0
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
    deadline = time.monotonic() + SCROLL_TOTAL_MS / 1000.0
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


def _target_label(t) -> str:
    """One readable token for an action target: selector, or [index] (+expect)."""
    if isinstance(t, str):
        return repr(t)
    if (t or {}).get("index") is not None:
        base = f"[{t['index']}]"
        return base + (f" ~{t['expect']}" if t.get("expect") else "")
    return repr((t or {}).get("selector"))


def action_label(a: dict) -> str:
    """One short line naming an action, for the outcome receipt in the response."""
    (key, val), = a.items()
    if key == "fill":
        return f"fill {_target_label(val)}"
    if key == "press":
        target = val.get("selector")
        if target is None and val.get("index") is not None:
            target = f"[{val['index']}]"
        return f"press {val.get('key')!r}" + (f" in {target}" if target else "")
    if key == "scroll":
        return f"scroll x{val.get('steps')}"
    if key == "screenshot":
        return f"screenshot {_target_label(val)}" + (
            " + decode_qr" if isinstance(val, dict) and val.get("decode_qr") else "")
    if key == "snapshot":
        return "snapshot interactive elements"
    if key == "submit":
        # _target_label(val), not val.get("selector"): a submit addressed by
        # snapshot index normalizes to {"index": N} with NO "selector" key, so
        # the old .get("selector") produced "submit None (unconfirmed)" - and
        # that string is what DHOLE_USAGE_LOG's write-audit row recorded, i.e.
        # the audit trail lost the target of the very step it exists to record.
        base = f"submit {_target_label(val)}"
        return base + ("" if isinstance(val, dict) and val.get("confirm")
                       else " (unconfirmed)")
    if key == "click":
        return f"click {_target_label(val)}"
    if key == "wait_selector":
        sel = val if isinstance(val, str) else (val or {}).get("selector")
        return f"wait_selector {sel!r}"
    return f"{key} {val}"


def _action_failure(exc: Exception) -> str:
    """The first useful line of an action failure, in caller-facing terms.

    Playwright's own text is a call log; the part that matters here is which of
    the two things went wrong — nothing matched the selector, or it matched but
    never became actionable.
    """
    text = str(exc).strip()
    first = text.splitlines()[0][:160] if text else type(exc).__name__
    if "waiting for locator" in text or "no element" in text.lower():
        return ("the selector matched no element (or none became actionable) before "
                f"the timeout: {first}")
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
    """
    if not actions:
        return None
    validated = _validate_actions(actions)
    outcomes: list[dict] = []

    async def page_action(page) -> None:
        outcomes.clear()
        for a in validated:
            label = action_label(a)
            shot: dict = {}
            try:
                if "click" in a:
                    loc = await _target_locator(page, a["click"])
                    await _settle_after_click(page, loc)
                elif "fill" in a:
                    f = a["fill"]
                    loc = await _target_locator(page, f)
                    await loc.fill(f["text"], timeout=10_000)
                elif "press" in a:
                    p = a["press"]
                    if p.get("index") is not None:
                        loc = await _target_locator(page, p)
                        await loc.press(p["key"], timeout=10_000)
                    elif p.get("selector"):
                        await page.locator(p["selector"]).first.press(
                            p["key"], timeout=10_000)
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
                            timeout=w["timeout"])
                    else:
                        await page.locator(w["selector"]).first.wait_for(
                            state=w["state"], timeout=w["timeout"])
                elif "screenshot" in a:
                    import base64
                    s = a["screenshot"]
                    loc = await _target_locator(page, s)
                    data = await loc.screenshot(type="png", timeout=10_000)
                    if len(data) > MAX_ACTION_SHOT_BYTES:
                        raise ValueError(
                            f"element capture is {len(data):,} bytes "
                            f"(cap {MAX_ACTION_SHOT_BYTES:,}) - crop a smaller "
                            "element, not a whole-page container")
                    shot["image_b64"] = base64.b64encode(data).decode("ascii")
                    shot["image_mime"] = "image/png"
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
                         **({"input_type": r[5]} if r[5] else {})}
                        for r in rows if isinstance(r, list) and len(r) >= 6]
                    shot["elements_total"] = total
                    if truncated:
                        # 截断要如实说：agent 以为看到全部才会把"没有"当成"不存在"。
                        shot["elements_truncated"] = True
                elif "submit" in a:
                    s = a["submit"]
                    if not s.get("confirm"):
                        # 审批闸（安全红线 3）：提交是不可逆的一步。暂停而不是
                        # 执行——行内标记 pending_approval，其余动作照常跑完
                        # （页面停在提交前的状态），agent 转达用户，确认后带
                        # {"confirm": true} 重跑同一条 actions 链。
                        outcomes.append({
                            "action": label, "ok": False, "pending_approval": True,
                            "error": ("pending_approval: submitting is an irreversible "
                                      "step - ask the user to confirm this submission, "
                                      "then re-run the same actions with "
                                      '{"confirm": true} on this submit. Nothing was '
                                      "submitted."),
                        })
                        continue
                    loc = await _target_locator(page, s)
                    await _settle_after_click(page, loc)
                    shot["submitted"] = True
                outcomes.append({"action": label, "ok": True, **shot})
            except Exception as e:
                outcomes.append({"action": label, "ok": False,
                                 "error": _action_failure(e)})
                # `label`, never `a`: a fill action's dict carries its `text`,
                # and that text can be a password (the login flow builds exactly
                # such a chain). Logging the raw action wrote the secret to the
                # log while the response, the receipt and the audit row were all
                # careful not to - the one channel nobody audits was the leak.
                # The label names the target and never the value, so it is what
                # every other consumer of this action already records.
                logger.warning("page action %s failed: %s", label, str(e)[:160])

    page_action.outcomes = outcomes  # type: ignore[attr-defined]
    return page_action
