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
_VALID_KEYS = {"click", "fill", "press", "wait", "scroll", "wait_selector"}


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
        if key in ("click",):
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"action {i} {key!r} must be a non-empty CSS selector string")
            validate_css_selector(val)  # raises SecurityError on bad selector
            out.append({key: val})
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
        elif key == "fill":
            if not isinstance(val, dict) or "selector" not in val or "text" not in val:
                raise ValueError(f"action {i} 'fill' must be {{selector, text}}")
            sel = val["selector"]
            if not isinstance(sel, str) or not sel.strip():
                raise ValueError(f"action {i} 'fill.selector' must be a non-empty CSS selector")
            validate_css_selector(sel)
            text = val["text"]
            if not isinstance(text, str):
                raise ValueError(f"action {i} 'fill.text' must be a string")
            if len(text) > 5000:
                raise ValueError(f"action {i} 'fill.text' too long (max 5000 chars)")
            out.append({key: {"selector": sel, "text": text}})
        elif key == "press":
            if isinstance(val, str):
                k = val.strip()
                if not k:
                    raise ValueError(f"action {i} 'press' key is empty")
                out.append({key: {"selector": None, "key": k[:50]}})
            elif isinstance(val, dict) and "key" in val:
                sel = val.get("selector")
                k = str(val.get("key", "")).strip()
                if not k:
                    raise ValueError(f"action {i} 'press.key' is empty")
                if sel is not None and (not isinstance(sel, str) or not sel.strip()):
                    raise ValueError(f"action {i} 'press.selector' must be a non-empty CSS selector")
                if sel:
                    validate_css_selector(sel)
                out.append({key: {"selector": sel, "key": k[:50]}})
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


async def _click_and_settle(page, selector: str) -> None:
    """Click, then give whatever the click caused time to finish.

    ``locator().click()`` returns once the event is dispatched, not once its
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
    await page.locator(selector).first.click(timeout=10_000)
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


def action_label(a: dict) -> str:
    """One short line naming an action, for the outcome receipt in the response."""
    (key, val), = a.items()
    if key == "fill":
        return f"fill {val.get('selector')!r}"
    if key == "press":
        target = val.get("selector")
        return f"press {val.get('key')!r}" + (f" in {target!r}" if target else "")
    if key == "scroll":
        return f"scroll x{val.get('steps')}"
    if key in ("click", "wait_selector"):
        sel = val if isinstance(val, str) else (val or {}).get("selector")
        return f"{key} {sel!r}"
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
            try:
                if "click" in a:
                    await _click_and_settle(page, a["click"])
                elif "fill" in a:
                    f = a["fill"]
                    await page.locator(f["selector"]).first.fill(f["text"], timeout=10_000)
                elif "press" in a:
                    p = a["press"]
                    if p["selector"]:
                        await page.locator(p["selector"]).first.press(p["key"], timeout=10_000)
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
                outcomes.append({"action": label, "ok": True})
            except Exception as e:
                outcomes.append({"action": label, "ok": False,
                                 "error": _action_failure(e)})
                logger.warning("page action %a failed: %s", a, str(e)[:160])

    page_action.outcomes = outcomes  # type: ignore[attr-defined]
    return page_action
